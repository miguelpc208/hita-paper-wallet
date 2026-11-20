"""Local synthetic-only CLI for HITA A0. No external connections or broker execution."""
import argparse, json, sys
from .ledger import Ledger
from .schema import connect, apply_migrations


def build_gold001(path):
    l=Ledger(path=path,gold='001',variant='base')
    try:
        l.opening('BRK',cash={'EUR':'10000'})
        l.opening('BNK',cash={'EUR':'6000'})
        l.cashflow('t1out','-5000','BNK','TRANSFER_OUT');l.cashflow('t1in','5000','BRK','TRANSFER_IN');l.match_transfer('t1out','t1in')
        l.buy('buy1',qty='10',price='100',fee='1');l.settle('pay1',l.obligation(index=0),'1001')
        l.buy('buy2',qty='10',price='100',fee='1');l.settle('pay2',l.obligation(index=1),'1001')
        l.income('dividend','DIVIDEND',gross_amount='20',tax='5')
        l.split('split2to1','2','1')
        l.sell('sale5',qty='5',price='120',fee='2');l.settle('sale-settle',l.obligation(typ='RECEIVABLE'),'598')
        l.cashflow('t2out','-500','BRK','TRANSFER_OUT');l.cashflow('t2in','500','BNK','TRANSFER_IN');l.match_transfer('t2out','t2in')
        issues=l.audit_invariants()
        result={
            'mode':'SYNTHETIC_ONLY', 'fixture':'GOLD-001',
            'brk_cash_eur':str(l.balance_cash('BRK')),
            'bank_cash_eur':str(l.balance_cash('BNK')),
            'shares':str(l.qty()),
            'remaining_acquisition_cost_eur':str(sum((l.lot_cost(x['lot_id']) for x in l.lots()),start=0)),
            'realized_gain_eur':str(l.realized_gain()),
            'market_price_eur':'120',
            'consolidated_nav_eur':str(l.nav(('BRK','BNK'),{'EQ1':'120'})),
            'accepted_events':l.one("SELECT COUNT(*) n FROM led_financial_events WHERE event_status='ACCEPTED'")['n'],
            'accepted_postings':l.one('SELECT COUNT(*) n FROM led_event_postings')['n'],
            'sqlite_integrity':l.db.execute('PRAGMA integrity_check').fetchone()[0],
            'referential_financial_errors':issues,
        }
        assert result['consolidated_nav_eur']=='18811'
        assert result['accepted_events']==12 and result['accepted_postings']==23
        assert not issues
        l.finalize_batch()
        return result
    finally:l.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description='HITA A0 local synthetic-only accounting research')
    parser.add_argument('command',choices=('schema','demo','inspect','audit','reconcile'))
    parser.add_argument('--db',default=':memory:',help='Path to local synthetic SQLite database (no network)')
    parser.add_argument('--scenario', choices=('matched', 'breaks'), help='Closing statement fixture for reconcile')
    args=parser.parse_args(argv)
    if args.scenario is not None and args.command != 'reconcile':
        parser.error('--scenario is only available for reconcile')
    if args.command == 'reconcile':
        from .reconciliation_demo import build_reconciliation_demo
        if args.db != ':memory:':
            parser.error('reconcile uses its bundled in-memory fixture')
        result = build_reconciliation_demo(args.scenario or 'matched')
        print(json.dumps(result, indent=2))
        if result['status'] != 'RECONCILED':
            raise SystemExit(2)
    elif args.command=='schema':
        db=connect(args.db)
        try:
            migrations=apply_migrations(db)
            print(json.dumps({'mode':'SYNTHETIC_ONLY','migrations':migrations,'tables':30},indent=2))
        finally:db.close()
    elif args.command=='demo':
        print(json.dumps(build_gold001(args.db),indent=2))
    elif args.command=='audit':
        from .assurance import audit_database
        result=audit_database(args.db)
        print(json.dumps(result,indent=2))
        if result['issues']:
            raise SystemExit(2)
    elif args.command=='inspect':
        db=connect(args.db)
        try:
            has=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='led_financial_events'").fetchone()
            if not has:raise RuntimeError('A0 database not initialized')
            result={'mode':'SYNTHETIC_ONLY','tables':db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone()[0],
                'accepted_events':db.execute("SELECT COUNT(*) FROM led_financial_events WHERE event_status='ACCEPTED'").fetchone()[0],
                'postings':db.execute('SELECT COUNT(*) FROM led_event_postings').fetchone()[0],
                'integrity':db.execute('PRAGMA integrity_check').fetchone()[0],
                'foreign_key_issues':len(db.execute('PRAGMA foreign_key_check').fetchall())}
            print(json.dumps(result,indent=2))
        finally:db.close()

if __name__=='__main__': main()
