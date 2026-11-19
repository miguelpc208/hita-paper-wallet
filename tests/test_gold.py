"""The 21 named HITA A0 golden cases, synthetic and local only.
Each test checks postings/lot state/financial invariants beyond final NAV where applicable.
"""
import tempfile, os, threading, unittest
from decimal import Decimal as D
from hita_a0.ledger import Ledger, Rejected, Conflict
from hita_a0.money import dec, canonical, gross, quantize_cent, FinancialValidationError

class GoldTests(unittest.TestCase):
    def ledger(self, number, variant='base'):
        l=Ledger(gold=f'{number:03d}',variant=variant)
        self.addCleanup(l.close)
        return l
    def post_count(self,l):
        return l.one('SELECT count(*) n FROM led_event_postings')['n']
    def event_count(self,l):
        return l.one("SELECT count(*) n FROM led_financial_events WHERE event_status='ACCEPTED'")['n']
    def check(self,l,accounts=('BRK','BNK')):
        self.assertEqual(l.audit_invariants(accounts),[])
        self.assertEqual(l.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
    def test_GOLD_001_consolidated(self):
        l=self.ledger(1)
        l.opening('BRK',cash={'EUR':'10000'});l.opening('BNK',cash={'EUR':'6000'})
        l.cashflow('t1out','-5000','BNK','TRANSFER_OUT');l.cashflow('t1in','5000','BRK','TRANSFER_IN');l.match_transfer('t1out','t1in')
        l.buy('b1',fee='1');l.settle('s1',l.obligation(index=0),'1001')
        l.buy('b2',fee='1');l.settle('s2',l.obligation(index=1),'1001')
        l.income('div','DIVIDEND',gross_amount='20',tax='5')
        l.split('split','2','1')
        l.sell('sell',qty='5',price='120',fee='2');l.settle('sale-settle',l.obligation(typ='RECEIVABLE'),'598')
        l.cashflow('t2out','-500','BRK','TRANSFER_OUT');l.cashflow('t2in','500','BNK','TRANSFER_IN');l.match_transfer('t2out','t2in')
        self.assertEqual(l.balance_cash('BRK'),D('13111'))
        self.assertEqual(l.balance_cash('BNK'),D('1500'))
        self.assertEqual(l.qty(),D('35'))
        self.assertEqual(l.nav(('BRK','BNK'),{'EQ1':'120'}),D('18811'))
        self.assertEqual(sum(l.lot_cost(x['lot_id']) for x in l.lots()),D('1751.75'))
        self.assertEqual(l.realized_gain(),D('347.75'))
        self.assertEqual(self.post_count(l),23);self.assertEqual(self.event_count(l),12)
        self.check(l)
        l.buy('b1',fee='1')
        self.assertEqual(self.event_count(l),12);self.assertEqual(l.qty(),D('35'))
    def test_GOLD_002_partial_settlement(self):
        l=self.ledger(2);l.opening(cash={'EUR':'10000'});l.buy('b',fee='1');o=l.obligation()
        self.assertEqual(l.nav(price={'EQ1':'100'}),D('9999'))
        l.settle('s1',o,'600');self.assertEqual(l.progress(o)['state'],'OPEN_PARTIAL');self.assertEqual(l.progress(o)['U'],D('401'))
        self.assertEqual(l.nav(price={'EQ1':'100'}),D('9999'))
        l.settle('s2',o,'401');self.assertEqual(l.progress(o)['state'],'SETTLED_FULL')
        self.assertEqual(l.balance_cash(),D('8999'));self.assertEqual(self.post_count(l),7);self.check(l)
    def test_GOLD_003_opening_and_cutoff(self):
        l=self.ledger(3);l.opening(cash={'EUR':'1000'},positions=[{'qty':'10','cost':None}],pending=[{'type':'PAYABLE','amount':'500'}])
        self.assertEqual(l.nav(price={'EQ1':'100'}),D('1500'));self.assertIsNone(l.lot_cost(l.lots()[0]['lot_id']))
        l.settle('open-settlement',l.obligation(),'500')
        self.assertEqual(l.nav(price={'EQ1':'100'}),D('1500'));self.assertEqual(l.qty(),D('10'));self.check(l)
        # Included/excluded events at cutoff must have distinguishable expected effects.
        e=self.ledger(3,'cut-excluded');e.opening(cash={'EUR':'100'},excluded_ops=('deposit',));e.cashflow('deposit','10',when='2026-01-15T12:00:00Z')
        self.assertEqual(e.balance_cash(),D('110'));self.check(e)
        included=self.ledger(3,'cut-included');included.opening(cash={'EUR':'110'});self.assertEqual(included.balance_cash(),D('110'))
        self.assertEqual(self.event_count(included),0)
    def test_GOLD_004_unknown_cost(self):
        l=self.ledger(4);l.opening(cash={'EUR':'0'},positions=[{'qty':'15','cost':None}])
        l.sell('sale',qty='5',price='12',fee='1');l.settle('pay',l.obligation(typ='RECEIVABLE'),'59')
        self.assertEqual(l.qty(),D('10'));self.assertEqual(l.balance_cash(),D('59'));self.assertIsNone(l.realized_gain());self.check(l)
    def test_GOLD_005_fx(self):
        l=self.ledger(5);l.opening(cash={'EUR':'100','USD':'0'});l.fx('fx')
        self.assertEqual(l.balance_cash(currency='EUR'),D('9'));self.assertEqual(l.balance_cash(currency='USD'),D('100'))
        self.assertEqual(l.nav(fx={'EUR':'1','USD':'0.9'}),D('99'));self.assertEqual(self.post_count(l),4);self.check(l)
    def test_GOLD_006_split_fifo(self):
        l=self.ledger(6);l.opening(cash={'EUR':'0'},positions=[{'qty':'10','cost':'1001'}]);l.split('split','2','1')
        l.sell('sale',qty='5',price='120',fee='2');l.settle('pay',l.obligation(typ='RECEIVABLE'),'598')
        self.assertEqual(l.qty(),D('15'));self.assertEqual(l.realized_gain(),D('347.75'))
        self.assertEqual(l.lot_cost(l.lots()[0]['lot_id']),D('750.75'));self.assertEqual(self.post_count(l),6);self.check(l)
        m=self.ledger(6,'multilot');m.opening(cash={'EUR':'0'},positions=[{'qty':'5','cost':'547','lots':[{'qty':'3','cost':'303'},{'qty':'2','cost':'244'}]}])
        m.split('split','2','1');m.sell('sale',qty='7',price='70',fee='1');m.settle('pay',m.obligation(typ='RECEIVABLE'),'489')
        self.assertEqual(m.qty(),D('3'));self.assertEqual(m.realized_gain(),D('125'))
        self.assertEqual(sum(m.lot_cost(x['lot_id']) for x in m.lots()),D('183'));self.assertEqual(self.post_count(m),8);self.check(m)
    def test_GOLD_007_dividend(self):
        l=self.ledger(7);l.opening(cash={'EUR':'0'});l.income('div','DIVIDEND',gross_amount='20',tax='5',fee='1')
        self.assertEqual(l.balance_cash(),D('14'));self.assertEqual(self.post_count(l),3);self.check(l)
    def test_GOLD_008_corrections(self):
        l=self.ledger(8);l.opening(cash={'EUR':'0'})
        l.cashflow('credit','15',kind='CASH_CORRECTION');l.cashflow('reverse','-15',kind='CASH_CORRECTION');l.cashflow('replacement','18',kind='CASH_CORRECTION')
        self.assertEqual(l.balance_cash(),D('18'))
        l.cashflow('replacement','18',kind='CASH_CORRECTION')
        self.assertEqual(l.balance_cash(),D('18'));self.assertEqual(self.event_count(l),3);self.check(l)
        with self.assertRaises(Conflict):l.cashflow('replacement','19',kind='CASH_CORRECTION')
        self.assertEqual(l.balance_cash(),D('18'))
    def test_GOLD_009_distinct_equal_trades(self):
        l=self.ledger(9);l.opening(cash={'EUR':'3000'});l.buy('fillA',fee='1');l.buy('fillB',fee='1')
        self.assertEqual(l.qty(),D('20'));self.assertEqual(l.obligation_balance(),D('2002'))
        self.assertEqual(l.nav(price={'EQ1':'100'}),D('2998'))
        l.buy('fillA',fee='1');self.assertEqual(self.event_count(l),2);self.assertEqual(self.post_count(l),6);self.check(l)
    def test_GOLD_010_two_fills(self):
        l=self.ledger(10);l.opening(cash={'EUR':'1000'});l.buy('fill1',qty='3',price='100',fee='0.30');l.buy('fill2',qty='2',price='101',fee='0.20')
        self.assertEqual(l.qty(),D('5'));self.assertEqual(l.obligation_balance(),D('502.50'))
        self.assertEqual(self.post_count(l),6);self.check(l)
    def test_GOLD_011_reject_invalid(self):
        l=self.ledger(11);l.opening(cash={'EUR':'100'})
        invalid=[lambda:l.buy('bad',qty='-1'), lambda:l.buy('bad',qty='10',price='NaN'),
                 lambda:l.buy('bad',qty=10.0),lambda:l.buy('bad',instrument='MISSING'),
                 lambda:l.cashflow('bad','0')]
        for case in invalid:
            with self.assertRaises((Rejected,FinancialValidationError)):case()
        self.assertEqual(self.event_count(l),0);self.assertEqual(l.balance_cash(),D('100'));self.check(l)
    def test_GOLD_012_overrun_and_concurrent(self):
        l=self.ledger(12);l.opening(cash={'EUR':'200'},pending=[{'type':'PAYABLE','amount':'100'}]);o=l.obligation()
        l.settle('p60',o,'60');self.assertEqual(l.progress(o)['U'],D('40'))
        with self.assertRaises(Rejected):l.settle('p50',o,'50')
        l.settle('p60',o,'60');self.assertEqual(l.balance_cash(),D('140'));self.assertEqual(self.event_count(l),1)
        self.assertEqual(l.nav(),D('100'));self.check(l)
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,'race.sqlite')
            setup=Ledger(path=path,gold='012',variant='concurrent');setup.opening(cash={'EUR':'200'},pending=[{'type':'PAYABLE','amount':'100'}]);oid=setup.obligation();setup.close()
            barrier=threading.Barrier(2);results=[]
            def worker(op):
                x=Ledger(path=path,gold='012',variant='concurrent')
                try:
                    barrier.wait()
                    x.settle(op,oid,'60');results.append('accepted')
                except Rejected:results.append('rejected')
                finally:x.close()
            a=threading.Thread(target=worker,args=('raceA',));b=threading.Thread(target=worker,args=('raceB',))
            a.start();b.start();a.join(timeout=10);b.join(timeout=10)
            self.assertFalse(a.is_alive(), 'First settlement thread did not terminate')
            self.assertFalse(b.is_alive(), 'Second settlement thread did not terminate')
            self.assertEqual(sorted(results),['accepted','rejected'])
            verify=Ledger(path=path,gold='012',variant='concurrent')
            try:
                self.assertEqual(verify.progress(oid)['U'],D('40'))
                self.assertEqual(verify.balance_cash(),D('140'))
                self.check(verify)
            finally:
                # On Windows, TemporaryDirectory cannot delete an open SQLite DB.
                # unittest.addCleanup runs too late, after this `with` block exits.
                verify.close()
    def test_GOLD_013_in_transit(self):
        l=self.ledger(13);l.opening('BNK',cash={'EUR':'1000'});l.opening('BRK',cash={'EUR':'0'})
        l.cashflow('out','-500','BNK','TRANSFER_OUT')
        self.assertEqual(l.balance_cash('BNK')+l.balance_cash('BRK'),D('500'))
        l.cashflow('in','500','BRK','TRANSFER_IN');l.match_transfer('out','in')
        self.assertEqual(l.balance_cash('BNK')+l.balance_cash('BRK'),D('1000'));self.assertEqual(self.post_count(l),2);self.check(l)
    def test_GOLD_014_decimal(self):
        l=self.ledger(14);l.opening(cash={'EUR':'0'});[l.cashflow('c'+str(i),'0.10') for i in range(3)]
        self.assertEqual(l.balance_cash(),D('0.30'))
        self.assertEqual(quantize_cent(D('1.005')),D('1.00'))
        self.assertEqual(quantize_cent(D('1.015')),D('1.02'))
        self.assertEqual(gross('3','0.333'),D('1.00'))
        self.assertEqual(canonical(dec('0.10')),'0.1')
        for s in ('0.123456789','10000000000000000.00000000','NaN','Infinity','1e-3'):
            with self.assertRaises(FinancialValidationError):dec(s)
        self.check(l)
    def test_GOLD_015_interest(self):
        l=self.ledger(15);l.opening(cash={'EUR':'100'});l.income('interest','INTEREST',gross_amount='10',tax='2')
        self.assertEqual(l.balance_cash(),D('108'));self.assertEqual(self.post_count(l),2);self.check(l)
    def test_GOLD_016_charges(self):
        l=self.ledger(16);l.opening(cash={'EUR':'1000'});l.charge('fee','STANDALONE_FEE','2');l.charge('tax','STANDALONE_TAX','3')
        self.assertEqual(l.balance_cash(),D('995'));self.assertEqual(self.post_count(l),4);self.check(l)
    def test_GOLD_017_external_cashflow(self):
        l=self.ledger(17);l.opening(cash={'EUR':'100'});l.cashflow('dep','200');l.cashflow('wd','-50',kind='EXTERNAL_WITHDRAWAL')
        self.assertEqual(l.balance_cash(),D('250'));self.check(l)
    def test_GOLD_018_reverse_settled_buy(self):
        l=self.ledger(18);l.opening(cash={'EUR':'10000'});l.buy('buy',fee='1');l.settle('settle-buy',l.obligation(),'1001')
        self.assertEqual(l.nav(price={'EQ1':'100'}),D('9999'))
        l.reverse_buy('rev-buy','buy','1001');self.assertEqual(l.qty(),D('0'))
        self.assertEqual(l.nav(),D('10000'))
        l.settle('refund',l.obligation(typ='RECEIVABLE'),'1001')
        self.assertEqual(l.balance_cash(),D('10000'))
        self.assertEqual(l.lot_cost(l.lots()[0]['lot_id']),D('0'))
        self.assertEqual(self.post_count(l),10);self.check(l)
    def test_GOLD_019_reverse_settled_sell(self):
        l=self.ledger(19);l.opening(cash={'EUR':'0'},positions=[{'qty':'10','cost':'800'}]);l.sell('sell',qty='5',price='120',fee='2')
        l.settle('pay-sale',l.obligation(typ='RECEIVABLE'),'598')
        self.assertEqual(l.realized_gain(),D('198'));self.assertEqual(l.nav(price={'EQ1':'120'}),D('1198'))
        l.reverse_sell('rev','sell','598');self.assertEqual(l.realized_gain(),D('0'))
        self.assertEqual(l.nav(price={'EQ1':'120'}),D('1200'))
        l.settle('repay',l.obligation(typ='PAYABLE'),'598');self.assertEqual(l.balance_cash(),D('0'))
        self.assertEqual(l.qty(),D('10'));self.assertEqual(l.lot_cost(l.lots()[0]['lot_id']),D('800'));self.assertEqual(self.post_count(l),10);self.check(l)
    def test_GOLD_020_reverse_split(self):
        l=self.ledger(20);l.opening(cash={'EUR':'0'},positions=[{'qty':'10','cost':'120'}]);l.split('rs','1','2')
        self.assertEqual(l.qty(),D('5'));self.assertEqual(l.lot_cost(l.lots()[0]['lot_id']),D('120'))
        self.assertEqual(l.nav(price={'EQ1':'20'}),D('100'));self.check(l)
    def test_GOLD_021_cancel_and_NAV(self):
        l=self.ledger(21);l.opening(cash={'EUR':'1000'},pending=[{'type':'PAYABLE','amount':'100'}]);o=l.obligation()
        l.settle('s60',o,'60');l.cancel('c40',o,'40')
        self.assertEqual(l.progress(o)['state'],'CLOSED_MIXED');self.assertEqual(l.nav(),D('940'));self.check(l)
        x=self.ledger(21,'partial-then-pay');x.opening(cash={'EUR':'1000'},pending=[{'type':'PAYABLE','amount':'100'}]);oid=x.obligation()
        x.cancel('c30',oid,'30');self.assertEqual(x.progress(oid)['state'],'OPEN_PARTIAL')
        x.settle('s20',oid,'20');x.settle('s50',oid,'50')
        self.assertEqual(x.progress(oid)['state'],'CLOSED_MIXED');self.assertEqual(x.nav(),D('930'));self.check(x)
        f=self.ledger(21,'full-cancel');f.opening(cash={'EUR':'1000'},pending=[{'type':'PAYABLE','amount':'100'}]);oid=f.obligation()
        f.cancel('c100',oid,'100');self.assertEqual(f.progress(oid)['state'],'CANCELLED_FULL');self.assertEqual(f.nav(),D('1000'))
        with self.assertRaises(Rejected):f.settle('bad',oid,'1')
        self.check(f)
        s=self.ledger(21,'blocked-trade');s.opening(cash={'EUR':'1000'});s.buy('buy',qty='1',price='100')
        with self.assertRaises(Rejected):s.cancel('bad',s.obligation(),'100')
        self.assertEqual(s.qty(),D('1'));self.assertEqual(s.obligation_balance(),D('100'));self.check(s)
