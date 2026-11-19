"""Adversarial synthetic A0 regression checks; not independent oracle sign-off."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from decimal import Decimal as D
from hita_a0.ledger import Ledger, Rejected, Conflict


class AccountingHardeningTests(unittest.TestCase):
    def ledger(self, label):
        obj = Ledger(gold='902', variant=label)
        self.addCleanup(obj.close)
        return obj

    def test_obligation_selection_chronological_on_same_recorded_timestamp(self):
        """Windows timestamp ties must not reorder UUID5-named trade obligations."""
        l=self.ledger('same-recorded-timestamp')
        l.now=lambda: '2026-01-11T10:00:00.000000Z'
        l.opening(cash={'EUR':'3000'})
        l.buy('b1',fee='1')
        l.buy('b2',fee='1')
        first=l.rid('obligation','b1')
        second=l.rid('obligation','b2')
        self.assertEqual(l.obligation(index=0),first)
        self.assertEqual(l.obligation(index=1),second)
        # Settling the second obligation must not accidentally re-settle the first.
        l.settle('s1',first,'1001')
        l.settle('s2',l.obligation(index=1),'1001')
        self.assertEqual(l.progress(first)['U'],D('0'))
        self.assertEqual(l.progress(second)['U'],D('0'))
        self.assertEqual(l.balance_cash(),D('998'))
        self.assertEqual(l.audit_invariants(('BRK',)),[])

    def test_double_sale_reversal_blocked_and_no_phantom_shares(self):
        l=self.ledger('double-reverse')
        l.opening(positions=[{'qty':'10','cost':'800'}])
        l.sell('sell',qty='5',price='120',fee='2')
        l.settle('sale-payment',l.obligation(typ='RECEIVABLE'),'598')
        l.reverse_sell('reversal-a','sell','598')
        before=(l.qty(),l.lot_cost(l.lots()[0]['lot_id']),l.realized_gain(),l.obligation_balance(typ='PAYABLE'))
        self.assertEqual(before,(D('10'),D('800'),D('0'),D('598')))
        with self.assertRaisesRegex(Rejected,'already fully reversed'):
            l.reverse_sell('reversal-b','sell','598')
        self.assertEqual((l.qty(),l.lot_cost(l.lots()[0]['lot_id']),l.realized_gain(),l.obligation_balance(typ='PAYABLE')),before)
        self.assertEqual(l.one("SELECT count(*) n FROM led_financial_events WHERE event_subtype='REVERSE_SETTLED_SELL'")['n'],1)
        self.assertEqual(l.audit_invariants(),[])

    def test_same_reversal_operation_idempotent(self):
        l=self.ledger('same-reverse')
        l.opening(positions=[{'qty':'10','cost':'800'}])
        l.sell('sell',qty='5',price='120',fee='2')
        l.settle('pay',l.obligation(typ='RECEIVABLE'),'598')
        first=l.reverse_sell('rev','sell','598')
        second=l.reverse_sell('rev','sell','598')
        self.assertEqual(first,second)
        self.assertEqual(l.qty(),D('10'))
        self.assertEqual(l.one("SELECT count(*) n FROM led_financial_events WHERE event_subtype='REVERSE_SETTLED_SELL'")['n'],1)

    def test_staged_allocation_does_not_modify_accepted_cost(self):
        l=self.ledger('staged-allocation')
        l.opening(positions=[{'qty':'10','cost':'800'}])
        lot=l.lots()[0]['lot_id']
        original_cost=l.lot_cost(lot)
        eid=l.rid('event','staged-alloc')
        l.db.execute("""INSERT INTO led_financial_events (
            event_id,account_id,source_id,accounting_template_id,event_class,event_subtype,
            provider_operation_key,provider_operation_scope,economic_effective_at,available_at,recorded_at,
            event_status,event_content_sha256,event_terms_schema_code,event_terms_json)
            VALUES(?,?,?,?,'FINANCIAL','SELL','staged-alloc','A0_SYNTHETIC',?,?,?,
            'STAGED',?,'A0-EVENT-v1','{}')""",
            (eid,l.aid('BRK'),l.source_id,l.sid('template','eat-002'),l.t(1),l.t(1),l.now(),'0'*64))
        l._alloc(lot,eid,'DISPOSE','2','160','staged-alloc')
        self.assertEqual(l.lot_cost(lot),original_cost)
        self.assertEqual(l.qty(),D('10'))
        self.assertEqual(l.audit_invariants(),[])

    def test_opening_cannot_claim_known_and_unknown_cost_simultaneously(self):
        l=self.ledger('cost-conflict')
        for p in (
            {'qty':'10','cost':'100','lots':[{'qty':'5','cost':'50'},{'qty':'5','cost':None}]},
            {'qty':'10','cost':None,'lots':[{'qty':'5','cost':'50'},{'qty':'5','cost':None}]},
        ):
            with self.assertRaisesRegex(Rejected,'partial historical cost'):
                l.opening(positions=[p])
        self.assertEqual(l.one('SELECT count(*) n FROM led_opening_anchors')['n'],0)
        self.assertEqual(l.one('SELECT count(*) n FROM src_source_records')['n'],0)

    def test_failed_atomic_event_does_not_shift_next_fixture_timestamp(self):
        l=self.ledger('rolled-counter')
        l.opening(cash={'EUR':'1000'})
        l.buy('b',qty='2',price='100')
        with self.assertRaises(Rejected):
            l.sell('bad',qty='3',price='100')
        l.cashflow('dep','5')
        row=l.one('SELECT economic_effective_at FROM led_financial_events WHERE provider_operation_key=?',('dep',))
        self.assertEqual(row['economic_effective_at'],l.t(2))
        self.assertEqual(l.one('SELECT count(*) n FROM led_financial_events')['n'],2)

    def test_duplicate_operation_alias_across_accounts_rejected_cleanly(self):
        l=self.ledger('alias-collision')
        l.opening('BRK');l.opening('BNK')
        l.cashflow('shared','5',account='BRK')
        with self.assertRaisesRegex(Conflict,'unique fixture operation alias'):
            l.cashflow('shared','5',account='BNK')
        self.assertEqual(l.balance_cash('BRK'),D('5'))
        self.assertEqual(l.balance_cash('BNK'),D('0'))

    def test_event_earlier_than_opening_cutoff_rejected(self):
        l=self.ledger('before-opening')
        l.opening(cash={'EUR':'100'})
        with self.assertRaisesRegex(Rejected,'predates opening'):
            l.cashflow('duplicate','10',when='2026-01-09T23:59:59Z')
        self.assertEqual(l.balance_cash(),D('100'))
        self.assertEqual(l.one('SELECT count(*) n FROM led_financial_events')['n'],0)

    def test_exact_cutoff_requires_explicit_exclusion(self):
        l=self.ledger('cutoff-no-proof')
        l.opening(cash={'EUR':'100'})
        with self.assertRaisesRegex(Rejected,'exact-cutoff'):
            l.cashflow('cash','10',when='2026-01-10T00:00:00Z')
        self.assertEqual(l.balance_cash(),D('100'))
        permitted=self.ledger('cutoff-proof')
        permitted.opening(cash={'EUR':'100'},excluded_ops=('cash',))
        permitted.cashflow('cash','10',when='2026-01-10T00:00:00Z')
        self.assertEqual(permitted.balance_cash(),D('110'))

    def test_invalid_source_effective_times_rejected(self):
        l=self.ledger('bad-event-times');l.opening(cash={'EUR':'100'})
        for stamp in ('2026-02-30T00:00:00Z','2026-01-11','2026-01-11T10:00:00+00:00',
                      '2026-01-11T10:00:00.001Z','wrong'):
            with self.subTest(stamp=stamp):
                with self.assertRaises(Rejected):
                    l.cashflow('bad','10',when=stamp)
        self.assertEqual(l.one('SELECT count(*) n FROM led_financial_events')['n'],0)

    def test_historical_cash_before_opening_rejected(self):
        l=self.ledger('before-anchor-snapshot')
        l.opening(cash={'EUR':'100'},positions=[{'qty':'3','cost':'30'}])
        with self.assertRaisesRegex(Rejected,'predates available opening'):
            l.balance_cash(T='2026-01-09T23:00:00Z')
        with self.assertRaisesRegex(Rejected,'predates available opening'):
            l.qty(T='2026-01-09T23:00:00Z')
        self.assertEqual(l.qty(T='2026-01-10T00:00:00Z'),D('3'))
        self.assertEqual(l.balance_cash(T='2026-01-10T00:00:00Z'),D('100'))

    def test_unapproved_opening_blocks_financial_event(self):
        l=self.ledger('blocked-account')
        l.opening(cash={'EUR':'100'},certified=False)
        with self.assertRaisesRegex(Rejected,'not certified'):
            l.cashflow('deposit','5')
        self.assertEqual(l.one('SELECT count(*) n FROM led_financial_events')['n'],0)

    def test_event_without_opening_anchor_is_rejected(self):
        l=self.ledger('no-opening')
        with self.assertRaisesRegex(Rejected,'opening anchor required'):
            l.cashflow('deposit','5')
        self.assertEqual(l.one('SELECT count(*) n FROM led_financial_events')['n'],0)

    def test_reopening_keeps_deterministic_sequence(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'persistent.sqlite'
            first=Ledger(path=path,gold='902',variant='reopen-sequence')
            try:
                first.opening(cash={'EUR':'0'})
                first.cashflow('first','5')
                first.cashflow('second','7')
            finally:
                first.close()
            reopened=Ledger(path=path,gold='902',variant='reopen-sequence')
            try:
                reopened.cashflow('third','10')
                row=reopened.one('SELECT economic_effective_at FROM led_financial_events WHERE provider_operation_key=?',('third',))
                self.assertEqual(row['economic_effective_at'],reopened.t(3))
                self.assertEqual(reopened.balance_cash(),D('22'))
                self.assertEqual(reopened.audit_invariants(),[])
            finally:
                reopened.close()

    def test_subcent_posting_and_unknown_currency_fail_closed(self):
        l=self.ledger('currency-quantum')
        l.opening(cash={'EUR':'100'})
        with self.assertRaisesRegex(Rejected,'cent quantum'):
            l.cashflow('subcent','0.001')
        with self.assertRaisesRegex(Rejected,'currency'):
            l.fx('wrong-ccy',sold='EUR',bought='ABC')
        self.assertEqual(l.balance_cash(),D('100'))
        self.assertEqual(l.one('SELECT count(*) n FROM led_financial_events')['n'],0)

    def test_opening_subcent_is_rejected_atomically(self):
        l=self.ledger('opening-quantum')
        with self.assertRaisesRegex(Rejected,'cent quantum'):
            l.opening(cash={'EUR':'100.001'})
        self.assertEqual(l.one('SELECT count(*) n FROM led_opening_anchors')['n'],0)
        self.assertEqual(l.one('SELECT count(*) n FROM src_source_records')['n'],0)

    def test_read_only_audit_finds_source_hash_tampering(self):
        from hita_a0.assurance import audit_database
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'audit.sqlite'
            l=Ledger(path=path,gold='902',variant='audit-source')
            try:
                l.opening(cash={'EUR':'20'})
                l.cashflow('credit','5')
            finally:
                l.close()
            ok=audit_database(path)
            self.assertEqual(ok['status'],'PASS_LOCAL_CHECKS')
            self.assertEqual(ok['accepted_events'],1)
            attacker=sqlite3.connect(path)
            try:
                attacker.execute("UPDATE src_source_records SET record_content_sha256=? WHERE provider_operation_key='credit'",('0'*64,))
                attacker.commit()
            finally:
                attacker.close()
            fail=audit_database(path)
            self.assertEqual(fail['status'],'FAIL_LOCAL_CHECKS')
            self.assertEqual(fail['source_record_hash_mismatches'],1)

    def test_read_only_audit_detects_duplicate_sale_reversal(self):
        from hita_a0.assurance import audit_database
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'audit.sqlite'
            l=Ledger(path=path,gold='902',variant='audit-reverse')
            try:
                l.opening(positions=[{'qty':'10','cost':'800'}])
                l.sell('s',qty='5',price='120',fee='2')
                l.settle('s-pay',l.obligation(typ='RECEIVABLE'),'598')
                l.reverse_sell('r','s','598')
            finally:
                l.close()
            check=audit_database(path)
            self.assertEqual(check['status'],'PASS_LOCAL_CHECKS')
            self.assertEqual(check['double_sale_reversal_count'],0)
