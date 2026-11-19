"""Targeted negative and boundary variants of the original 21 GOLD identifiers."""
import unittest,sqlite3
from decimal import Decimal as D
from hita_a0.ledger import Ledger, Rejected

class GoldenVariants(unittest.TestCase):
    def ledger(self,number,variant):
        l=Ledger(gold=f'{number:03}',variant=variant);self.addCleanup(l.close);return l
    def test_gold003_unresolved_cutoff_blocks_certification(self):
        l=self.ledger(3,'cut-unresolved');l.opening(cash={'EUR':'100'},certified=False)
        self.assertEqual(l.one('SELECT anchor_status FROM led_opening_anchors')['anchor_status'],'BLOCKED')
        self.assertEqual(l.one('SELECT run_status FROM rec_reconciliation_runs')['run_status'],'BLOCKED')
        self.assertEqual(l.one('SELECT count(*) n FROM rec_reconciliation_breaks')['n'],1)
        with self.assertRaises(Rejected):l.nav()
    def test_gold008_versioned_source_known_asof(self):
        l=self.ledger(8,'supersession');l.opening(cash={'EUR':'0'})
        values=['15','-15','18','-18','20']
        for i,v in enumerate(values,1):l.cashflow('correction'+str(i),v,kind='CASH_CORRECTION',when=l.t(i))
        self.assertEqual(l.balance_cash(K='2026-01-11T12:30:00Z'),D('18'))
        self.assertEqual(l.balance_cash(K='2026-01-11T14:30:00Z'),D('20'))
        self.assertEqual(l.balance_cash(),D('20'))
    def test_gold011_quarantined_source_no_financial_effect(self):
        l=self.ledger(11,'disputed-evidence');l.opening(cash={'EUR':'100'})
        l.reject_synthetic_input('disputed',{'raw':'incomplete'},'ERR_EVIDENCE_DISPUTED',quarantine=True)
        row=l.one("SELECT classification_status,validation_error_code FROM src_source_records WHERE provider_operation_key='disputed'")
        self.assertEqual((row['classification_status'],row['validation_error_code']),('QUARANTINED','ERR_EVIDENCE_DISPUTED'))
        self.assertEqual(l.balance_cash(),D('100'))
        self.assertEqual(l.one('SELECT count(*) n FROM led_financial_events')['n'],0)
    def test_gold012_missing_cash_leg_rejected_in_sql(self):
        l=self.ledger(12,'missing-cash-leg');l.opening(cash={'EUR':'200'},pending=[{'type':'PAYABLE','amount':'100'}])
        # Deliberate direct staged row to verify the physical publication guard.
        orig=l.rid('event','candidate');aid=l.aid('BRK');template=l.sid('template','eat-003')
        src=l.reject_synthetic_input('preflight-only',{'amount':'20'},'PENDING_TEST')
        l.db.execute("INSERT INTO led_financial_events(event_id,account_id,source_id,instrument_id,accounting_template_id,event_class,event_subtype,provider_operation_key,provider_operation_scope,economic_effective_at,available_at,recorded_at,event_status,event_content_sha256,event_terms_schema_code,event_terms_json) VALUES(?,?,?,NULL,?,'FINANCIAL','SETTLE_PAYABLE','candidate','A0_SYNTHETIC',?,?,?,'STAGED',?,'A0-EVENT-v1','{}')",(
            orig,aid,l.source_id,template,l.t(1),l.t(1),l.now(),'0'*64))
        # A correct obligation-only posting is not enough; the paired cash debit is mandatory.
        l._post(orig,aid,template,l.t(1),'PAYABLE',-D('20'),obligation=l.obligation())
        l.db.execute("INSERT INTO src_record_event_links VALUES(?,?,?,'EVIDENCES','VERIFIED',?)",(l.rid('source-link','partial'),src,orig,l.now()))
        with self.assertRaises(sqlite3.IntegrityError):l.db.execute("UPDATE led_financial_events SET event_status='ACCEPTED' WHERE event_id=?",(orig,))
        self.assertEqual(l.obligation_balance(),D('100'))
    def test_gold012_terminal_settlement_rejected(self):
        l=self.ledger(12,'terminal');l.opening(cash={'EUR':'200'},pending=[{'type':'PAYABLE','amount':'100'}])
        oid=l.obligation();l.settle('pay100',oid,'100')
        with self.assertRaises(Rejected):l.settle('pay1',oid,'1')
        self.assertEqual(l.balance_cash(),D('100'));self.assertEqual(l.progress(oid)['state'],'SETTLED_FULL')
    def test_gold013_inflight_nav_not_certified(self):
        l=self.ledger(13,'intermediate');l.opening('BNK',cash={'EUR':'1000'});l.opening('BRK',cash={'EUR':'0'})
        l.cashflow('out','-500','BNK','TRANSFER_OUT')
        self.assertEqual(l.transfer_certification_status(('BNK','BRK')),'NOT_CERTIFIED_IN_FLIGHT_TRANSFER')
        with self.assertRaises(Rejected):l.nav(accounts=('BNK','BRK'))
        l.cashflow('in','500','BRK','TRANSFER_IN');l.match_transfer('out','in')
        self.assertEqual(l.nav(accounts=('BNK','BRK')),D('1000'))
    def test_gold014_round_half_even_and_fee_residual(self):
        a=self.ledger(14,'round-down-tie');a.opening(cash={'EUR':'100'});a.buy('b',qty='1',price='1.005')
        self.assertEqual(a.obligation_balance(),D('1.00'))
        b=self.ledger(14,'round-up-tie');b.opening(cash={'EUR':'100'});b.buy('b',qty='1',price='1.015')
        self.assertEqual(b.obligation_balance(),D('1.02'))
        c=self.ledger(14,'fee-residual');c.opening(cash={'EUR':'1000'})
        c.buy('fill1',qty='1',price='100',fee='0');c.buy('fill2',qty='1',price='200',fee='0.01')
        self.assertEqual(c.obligation_balance(),D('300.01'))
        self.assertEqual(c.one('SELECT COUNT(*) n FROM led_event_postings')['n'],5)
    def test_gold018_void_cost_and_no_sale(self):
        l=self.ledger(18,'void-physical');l.opening(cash={'EUR':'10000'});l.buy('buy',fee='1');l.settle('pay',l.obligation(),'1001');l.reverse_buy('reversal','buy','1001')
        row=l.one('SELECT allocation_action,allocation_method,allocated_cost_dec,allocated_quantity_dec FROM led_lot_allocations')
        self.assertEqual(row['allocation_action'],'VOID_ACQUISITION');self.assertIsNone(row['allocation_method'])
        self.assertEqual(row['allocated_cost_dec'],'1001');self.assertEqual(row['allocated_quantity_dec'],'10')
        self.assertEqual(l.realized_gain(),D('0'))
    def test_gold019_restoration_has_origin(self):
        l=self.ledger(19,'restore-physical');l.opening(cash={'EUR':'0'},positions=[{'qty':'10','cost':'800'}]);l.sell('sale',qty='5',price='120',fee='2');l.settle('pay',l.obligation(typ='RECEIVABLE'),'598');l.reverse_sell('reversal','sale','598')
        r=l.one("SELECT allocation_action,reverses_allocation_id,allocation_method FROM led_lot_allocations WHERE allocation_action='RESTORE'")
        self.assertEqual(r['allocation_action'],'RESTORE');self.assertIsNotNone(r['reverses_allocation_id']);self.assertIsNone(r['allocation_method'])
        self.assertEqual(l.realized_gain(),D('0'))
    def test_gold021_cancel_overrun_rejected(self):
        l=self.ledger(21,'cancel-overrun');l.opening(cash={'EUR':'1000'},pending=[{'type':'PAYABLE','amount':'100'}])
        oid=l.obligation()
        with self.assertRaises(Rejected):l.cancel('bad',oid,'110')
        self.assertEqual(l.progress(oid)['U'],D('100'));self.assertEqual(l.nav(),D('900'))
    def test_gold021_open_partial_cancel_no_cash(self):
        l=self.ledger(21,'partial-only');l.opening(cash={'EUR':'1000'},pending=[{'type':'PAYABLE','amount':'100'}])
        oid=l.obligation();l.cancel('c30',oid,'30')
        self.assertEqual((l.progress(oid)['S'],l.progress(oid)['C'],l.progress(oid)['U']),(D('0'),D('30'),D('70')))
        self.assertEqual(l.progress(oid)['state'],'OPEN_PARTIAL');self.assertEqual(l.balance_cash(),D('1000'))
        self.assertEqual(l.nav(),D('930'))
    def test_fixed_synthetic_source_hashes_and_final_tallies(self):
        l=self.ledger(1,'hashes');l.opening(cash={'EUR':'0'});l.cashflow('dep','5')
        l.finalize_batch()
        row=l.one('SELECT total_records,accepted_records,batch_status,finished_at FROM src_ingestion_batches')
        self.assertEqual(row['total_records'],row['accepted_records']);self.assertEqual(row['batch_status'],'ACCEPTED')
        self.assertIsNotNone(row['finished_at'])
        with self.assertRaises(Rejected):l.cashflow('too-late','5')
