"""Additional negative/structural assertions; no third-party services are exercised."""
import sqlite3, unittest
from datetime import datetime,timezone,timedelta
from decimal import Decimal as D
from hita_a0.ledger import Ledger, Rejected
from hita_a0.schema import apply_migrations
from hita_a0.money import FinancialValidationError, dec, canonical

class PhysicalIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.l=Ledger(gold='901',variant=self._testMethodName)
        self.addCleanup(self.l.close)
    def test_30_strict_tables_and_5_migrations(self):
        tables=self.l.all("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        self.assertEqual(len(tables),30)
        self.assertTrue(all(' STRICT' in row['sql'] for row in tables))
        self.assertEqual(self.l.one('SELECT count(*) n FROM gov_schema_migrations')['n'],5)
        self.assertEqual(len(apply_migrations(self.l.db)),5)
    def test_foreign_key_check(self):
        self.l.opening(cash={'EUR':'1000'});self.l.buy('b')
        self.assertEqual(self.l.db.execute('PRAGMA foreign_key_check').fetchall(),[])
    def test_no_mutation_accepted_event_or_posting(self):
        self.l.opening(cash={'EUR':'1000'});self.l.buy('b')
        e=self.l.rid('event','b')
        with self.assertRaises(sqlite3.IntegrityError):
            self.l.db.execute("UPDATE led_financial_events SET event_terms_json='{}' WHERE event_id=?",(e,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.l.db.execute("DELETE FROM led_financial_events WHERE event_id=?",(e,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.l.db.execute("DELETE FROM led_event_postings WHERE event_id=?",(e,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.l.db.execute("INSERT INTO led_event_postings SELECT ? ,event_id,account_id,accounting_template_id,99,posting_class,instrument_id,lot_id,settlement_obligation_id,obligation_type,currency_code,amount_dec,quantity_dec,effective_at,recorded_at,posting_evidence_reference FROM led_event_postings WHERE event_id=? LIMIT 1",(self.l.rid('posting','unauthorized'),e))
        self.assertEqual(self.l.qty(),D('10'))
    def test_origin_xor_rejects_incorrect_lot(self):
        self.l.opening(cash={'EUR':'0'},positions=[{'qty':'1','cost':'10'}])
        anchor=self.l.rid('anchor','brk');instrument=self.l.iid('EQ1');aid=self.l.aid('BRK')
        with self.assertRaises(sqlite3.IntegrityError):
            self.l.db.execute("INSERT INTO led_position_lots VALUES(?,?,?,'OPENING_POSITION',NULL,NULL,'1','KNOWN','10','1','EUR',?,?,?)",(
                self.l.rid('lot','invalid'),aid,instrument,'2026-01-10T00:00:00Z',anchor,'2026-01-10T00:00:00Z'))
    def test_origin_xor_rejects_incorrect_obligation(self):
        self.l.opening(cash={'EUR':'100'})
        with self.assertRaises(sqlite3.IntegrityError):
            self.l.db.execute("INSERT INTO led_settlement_obligations VALUES(?,'OPENING_ORIGIN',NULL,NULL,?,'EUR','PAYABLE','100',?,NULL,'VERIFIED','CURRENT',NULL,?,?)",(
                self.l.rid('obligation','invalid'),self.l.aid('BRK'),'2026-01-10T00:00:00Z','invalid','2026-01-10T00:00:00Z'))
    def test_roll_back_failed_sell(self):
        self.l.opening(cash={'EUR':'1000'});self.l.buy('buy',qty='2',price='100')
        baseline=(self.l.qty(),self.l.one("SELECT count(*) n FROM led_financial_events")['n'],self.l.one('SELECT count(*) n FROM led_event_postings')['n'])
        with self.assertRaises(Rejected):self.l.sell('oversell',qty='3',price='100')
        self.assertEqual((self.l.qty(),self.l.one('SELECT count(*) n FROM led_financial_events')['n'],self.l.one('SELECT count(*) n FROM led_event_postings')['n']),baseline)
        self.assertEqual(self.l.audit_invariants(),[])
    def test_unverified_source_missing_blocks_acceptance(self):
        self.l.opening(cash={'EUR':'1000'});self.l.buy('buy')
        orig=self.l.rid('event','buy')
        new=self.l.rid('event','staged-orphan')
        self.l.db.execute("INSERT INTO led_financial_events SELECT ?,account_id,source_id,instrument_id,accounting_template_id,event_class,event_subtype,?,provider_operation_scope,parent_order_reference,economic_effective_at,available_at,recorded_at,'STAGED',correction_of_event_id,event_content_sha256,event_terms_schema_code,event_terms_json,executed_quantity_dec,executed_unit_price_dec,trade_currency_code,gross_amount_dec,fee_amount_dec,tax_amount_dec,net_amount_dec FROM led_financial_events WHERE event_id=?",(new,'staged-orphan',orig))
        with self.assertRaises(sqlite3.IntegrityError):self.l.db.execute("UPDATE led_financial_events SET event_status='ACCEPTED' WHERE event_id=?",(new,))
        self.assertEqual(self.l.one('SELECT event_status FROM led_financial_events WHERE event_id=?',(new,))['event_status'],'STAGED')
    def test_wrong_account_lot_composite_fk(self):
        self.l.opening('BRK',cash={'EUR':'1000'});self.l.opening('BNK',cash={'EUR':'1000'})
        self.l.buy('buy')
        original=self.l.rid('event','buy')
        new=self.l.rid('event','bnk-staged')
        bnk=self.l.aid('BNK')
        self.l.db.execute("INSERT INTO led_financial_events SELECT ?,?,source_id,instrument_id,accounting_template_id,event_class,event_subtype,?,provider_operation_scope,parent_order_reference,economic_effective_at,available_at,recorded_at,'STAGED',correction_of_event_id,event_content_sha256,event_terms_schema_code,event_terms_json,executed_quantity_dec,executed_unit_price_dec,trade_currency_code,gross_amount_dec,fee_amount_dec,tax_amount_dec,net_amount_dec FROM led_financial_events WHERE event_id=?",(new,bnk,'bnk-staged',original))
        lot=self.l.lots()[0]['lot_id'];template=self.l.sid('template','eat-001')
        with self.assertRaises(sqlite3.IntegrityError):
            self.l.db.execute("INSERT INTO led_event_postings VALUES(?,?,?,?,1,'SECURITY_QTY',?,?,NULL,NULL,NULL,NULL,'1',?,?,?)",(
                self.l.rid('posting','invalid-account'),new,bnk,template,self.l.iid('EQ1'),lot,self.l.t(1),self.l.t(1),new))
    def test_nav_missing_price_must_fail(self):
        self.l.opening(cash={'EUR':'1000'},positions=[{'qty':'2','cost':'10'}])
        with self.assertRaises(Rejected):self.l.nav()
    def test_nav_duplicate_account_must_fail(self):
        self.l.opening(cash={'EUR':'1000'})
        with self.assertRaises(Rejected):self.l.nav(accounts=('BRK','BRK'))
    def test_temporal_as_known(self):
        self.l.opening(cash={'EUR':'0'})
        for i,v in enumerate(('15','-15','18','-18','20'),1):
            self.l.cashflow('c'+str(i),v,kind='CASH_CORRECTION',when=self.l.t(i))
        K3=(datetime.fromisoformat(self.l.t(3).replace('Z','+00:00'))+timedelta(minutes=30)).isoformat().replace('+00:00','Z')
        K5=(datetime.fromisoformat(self.l.t(5).replace('Z','+00:00'))+timedelta(minutes=30)).isoformat().replace('+00:00','Z')
        self.assertEqual(self.l.balance_cash(K=K3),D('18'))
        self.assertEqual(self.l.balance_cash(K=K5),D('20'))
    def test_decimal_lexical_rejection(self):
        for invalid in ('-01','+1','00','1.000000000','1e3','NaN','Infinity'):
            with self.assertRaises(FinancialValidationError):dec(invalid)
        self.assertEqual(canonical(dec('-0.00')),'0')
