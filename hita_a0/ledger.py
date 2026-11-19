"""Minimal A0 accounting kernel: source-evidenced synthetic transactions and exact-Decimal projection.
No HTTP requests, credentials, brokerage APIs, order submission, background jobs or live market feeds.
"""
from __future__ import annotations
from decimal import Decimal
from hashlib import sha256
from uuid import uuid5, NAMESPACE_DNS
from datetime import datetime, timezone, timedelta
import json
import re
from .schema import connect, apply_migrations
from .money import dec, canonical, quantity, gross, quantize_cent, FinancialValidationError

class Rejected(FinancialValidationError): pass
class Conflict(Rejected): pass

EAT_TYPES={
'BUY':'EAT-001','SELL':'EAT-002','SETTLE_PAYABLE':'EAT-003','SETTLE_RECEIVABLE':'EAT-003',
'DIVIDEND':'EAT-004','INTEREST':'EAT-005','STANDALONE_FEE':'EAT-006','STANDALONE_TAX':'EAT-007',
'EXTERNAL_DEPOSIT':'EAT-008','EXTERNAL_WITHDRAWAL':'EAT-008',
'TRANSFER_OUT':'EAT-009','TRANSFER_IN':'EAT-009','FX_CONVERSION':'EAT-010',
'SPLIT':'EAT-011','REVERSE_SPLIT':'EAT-011','PARTIAL_FILL_BUY':'EAT-012',
'CASH_CORRECTION':'EAT-013','REVERSE_SETTLED_BUY':'EAT-013','REVERSE_SETTLED_SELL':'EAT-013',
'CANCEL_UNSETTLED_PAYABLE':'EAT-013','CANCEL_UNSETTLED_RECEIVABLE':'EAT-013'
}
UTC='2026-01-10T00:00:00Z'
A0_CURRENCIES=frozenset(('EUR','USD'))
CANONICAL_EVENT_UTC = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')

def validate_utc_seconds(value):
    """Synthetic A0 comparisons require canonical, lexically sortable UTC seconds."""
    if not isinstance(value, str) or not CANONICAL_EVENT_UTC.fullmatch(value):
        raise Rejected('economic timestamp must be YYYY-MM-DDTHH:MM:SSZ')
    try:
        parsed=datetime.strptime(value,'%Y-%m-%dT%H:%M:%SZ')
    except ValueError as exc:
        raise Rejected('invalid economic UTC timestamp') from exc
    if parsed.strftime('%Y-%m-%dT%H:%M:%SZ') != value:
        raise Rejected('noncanonical economic UTC timestamp')
    return value


def digest(value):
    if not isinstance(value,str):
        value=json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'))
    return sha256(value.encode('utf-8')).hexdigest()

def cstr(value):
    return canonical(value if isinstance(value,Decimal) else dec(value))

class Ledger:
    def __init__(self, path=':memory:', gold='001', variant='base'):
        self.db=connect(path)
        self.gold=str(gold).zfill(3)
        self.variant=variant
        self.prefix=f'hita.a0.fixture.{self.gold}.v01.{self.variant}'
        self.seed_prefix='hita.a0.seed.v01'
        apply_migrations(self.db)
        self._seed()
        # A reopened ledger must continue the deterministic fixture timeline.
        # Count actual accepted events linked to this fixture's synthetic source batch.
        self._op_no=self.one('''SELECT COUNT(DISTINCT e.event_id) AS n
            FROM led_financial_events e
            JOIN src_record_event_links l ON l.event_id=e.event_id
            JOIN src_source_records r ON r.source_record_id=l.source_record_id
            WHERE r.batch_id=? AND e.event_status='ACCEPTED' ''',(self.batch_id,))['n']

    def close(self): self.db.close()
    def rid(self, family,alias): return str(uuid5(NAMESPACE_DNS,f'{self.prefix}.{family}.{alias}'))
    def sid(self, family,alias): return str(uuid5(NAMESPACE_DNS,f'{self.seed_prefix}.{family}.{alias}'))
    def now(self): return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
    def t(self,n): return (datetime(2026,1,11,9,tzinfo=timezone.utc)+timedelta(hours=n)).isoformat(timespec='seconds').replace('+00:00','Z')
    def one(self,q,args=()): return self.db.execute(q,args).fetchone()
    def all(self,q,args=()): return self.db.execute(q,args).fetchall()

    def _seed(self):
        db=self.db; db.execute('BEGIN IMMEDIATE')
        try:
            source_id=self.sid('data-source','synthetic-a0')
            db.execute("INSERT OR IGNORE INTO src_data_sources VALUES(?,?,'Synthetic HITA A0','SYNTHETIC','INTERNAL',1,?,'Synthetic only')",(source_id,'SYNTHETIC-A0',UTC))
            contract_id=self.sid('source-contract','synthetic-a0-ledger.v1')
            db.execute("INSERT OR IGNORE INTO src_source_contracts VALUES(?,?,?,?,?,?,NULL,'APPROVED',?,'A0 fixture contract')",(contract_id,source_id,'SYNTHETIC-A0-LEDGER','v1',digest('synthetic-schema-v1'),UTC,UTC))
            issuer_id=self.sid('issuer','issuer-eq1')
            db.execute("INSERT OR IGNORE INTO ref_issuers VALUES(?,?,'Synthetic Issuer',NULL,'ACTIVE',?,NULL)",(issuer_id,'ISSUER-EQ1',UTC))
            inst_id=self.sid('instrument','eq1')
            db.execute("INSERT OR IGNORE INTO ref_instruments VALUES(?,?,?,'Synthetic Equity','EQUITY',NULL,'EUR',1,'ACTIVE',?)",(inst_id,issuer_id,'EQ1',UTC))
            for a,kind in [('BRK','BROKERAGE'),('BNK','BANK')]:
                db.execute('INSERT OR IGNORE INTO ref_accounts VALUES(?,?,?,\'EUR\',\'synthetic-owner\',NULL,\'ACTIVE\',1,?)',(self.sid('account',a.lower()),a,kind,UTC))
            for p,typ in [('P-BRK','ACCOUNT'),('P-ALL','CONSOLIDATED_WEALTH')]:
                db.execute("INSERT OR IGNORE INTO ref_reporting_perimeters VALUES(?,?,?,'EUR',?,NULL,'ACTIVE',?)",(self.sid('perimeter',p.lower()),p,typ,UTC,UTC))
            for p,a in [('P-BRK','BRK'),('P-ALL','BRK'),('P-ALL','BNK')]:
                db.execute('INSERT OR IGNORE INTO ref_perimeter_memberships VALUES(?,?,?,?,NULL,\'INCLUDED\',NULL,?,?)',(
                    self.sid('membership',p.lower()+'.'+a.lower()),self.sid('perimeter',p.lower()),self.sid('account',a.lower()),UTC,UTC,UTC))
            for i in range(1,14):
                code=f'EAT-{i:03}'
                db.execute("INSERT OR IGNORE INTO led_event_accounting_templates VALUES(?,?,?,?,?,'{}','APPROVED',?,?,?)",(
                    self.sid('template',code.lower()),code,'v0.3','FINANCIAL',digest(code+'v0.3'),UTC,UTC,UTC))
            # One actual UTF8 artifact in memory, with a genuinely calculated SHA256 of its known content.
            content=f'HITA A0 synthetic fixture {self.gold}/{self.variant}\n'
            self.artifact_id=self.rid('artifact','fixture')
            db.execute("INSERT OR IGNORE INTO src_source_artifacts VALUES(?,?,?,?,?,?,?,NULL,'SYNTHETIC','INTERNAL','VERIFIED')",(
                self.artifact_id,source_id,self.prefix, 'synthetic://'+self.gold+'/'+self.variant,digest(content),len(content.encode('utf-8')),UTC))
            self.batch_id=self.rid('batch','fixture')
            db.execute("INSERT OR IGNORE INTO src_ingestion_batches(batch_id,artifact_id,contract_id,attempt_no,started_at,batch_status,total_records,accepted_records,duplicate_records,rejected_records,quarantined_records,pending_records) VALUES(?,?,?,?,?,'STARTED',0,0,0,0,0,0)",(
                self.batch_id,self.artifact_id,contract_id,1,UTC))
            db.commit()
        except:
            db.rollback();raise
        self.source_id=source_id

    def aid(self,account):
        row=self.one('SELECT account_id FROM ref_accounts WHERE account_key=?',(account,))
        if row is None: raise Rejected('unknown synthetic account')
        return row['account_id']
    def iid(self,instrument):
        row=self.one('SELECT instrument_id FROM ref_instruments WHERE instrument_key=?',(instrument,))
        if row is None: raise Rejected('unknown synthetic instrument')
        return row['instrument_id']
    def _audit(self,op,entity,ref, outcome='ACCEPTED'):
        a_id=self.rid('audit',op+'.'+outcome.lower())
        self.db.execute('INSERT OR IGNORE INTO aud_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(
            a_id,self.now(),self.now(),'SYNTHETIC_WRITER',op,entity,ref,None,'INFO' if outcome=='ACCEPTED' else 'WARNING',outcome,'{}',digest('{}')))
        return a_id
    def reject_synthetic_input(self,op,payload,reason,quarantine=False):
        """Record invalid synthetic evidence without creating an accepted financial event."""
        status='QUARANTINED' if quarantine else 'REJECTED'
        self.db.execute('BEGIN IMMEDIATE')
        try:
            src=self._source(op,payload,status)
            self.db.execute('UPDATE src_source_records SET validation_error_code=? WHERE source_record_id=?',(reason,src))
            self._audit('rejected.'+op,'src_source_records',src,'REJECTED')
            self.db.commit()
            return src
        except:
            self.db.rollback();raise

    def finalize_batch(self):
        """Close the synthetic ingestion batch after all source attempts are complete."""
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.one('SELECT * FROM src_ingestion_batches WHERE batch_id=?',(self.batch_id,))
            n=sum(row[k] for k in ('accepted_records','duplicate_records','rejected_records','quarantined_records','pending_records'))
            if row['total_records']!=n:raise Rejected('ingestion count mismatch')
            if row['batch_status']!='STARTED':raise Rejected('batch already finalized')
            self.db.execute("UPDATE src_ingestion_batches SET batch_status='ACCEPTED',finished_at=? WHERE batch_id=?",(self.now(),self.batch_id))
            self.db.commit()
        except:
            self.db.rollback();raise

    def _source(self,op,payload,classification='ACCEPTED'):
        if self.one('SELECT batch_status FROM src_ingestion_batches WHERE batch_id=?',(self.batch_id,))['batch_status']!='STARTED':
            raise Rejected('batch closed: new evidence requires new batch')
        idx=self.one('SELECT COUNT(*) AS n FROM src_source_records WHERE batch_id=?',(self.batch_id,))['n']+1
        alias=f'{op}.{idx}'
        srid=self.rid('source',alias)
        self.db.execute('INSERT INTO src_source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(
            srid,self.batch_id,alias,digest(payload),digest(payload),op,'A0_SYNTHETIC',payload.get('when',self.now()),op,classification,
            None if classification=='ACCEPTED' else ('DUPLICATE' if classification=='DUPLICATE' else 'ERR_REJECTED'),
            json.dumps(payload,sort_keys=True,separators=(',',':'))))
        field={'ACCEPTED':'accepted_records','DUPLICATE':'duplicate_records','REJECTED':'rejected_records','QUARANTINED':'quarantined_records'}.get(classification,'pending_records')
        self.db.execute('UPDATE src_ingestion_batches SET total_records=total_records+1,'+field+'='+field+'+1 WHERE batch_id=?',(self.batch_id,))
        return srid

    def opening(self,account='BRK',cash=None,positions=None,pending=None,cutoff=UTC,certified=True,excluded_ops=()):
        cash={'EUR':'0'} if cash is None else cash
        positions=[] if positions is None else positions
        pending=[] if pending is None else pending
        cutoff=validate_utc_seconds(cutoff)
        excluded_ops=tuple(excluded_ops)
        if len(set(excluded_ops))!=len(excluded_ops) or any(not isinstance(op,str) or not op for op in excluded_ops):
            raise Rejected('invalid synthetic cutoff exclusion manifest')
        db=self.db; account_id=self.aid(account)
        if self.one('SELECT 1 FROM led_opening_anchors WHERE account_id=?',(account_id,)):
            raise Rejected('opening anchor already exists for account')
        db.execute('BEGIN IMMEDIATE')
        try:
            source_payload={'account':account,'cash':cash,'positions':positions,'pending':pending,'cutoff':cutoff,'excluded_ops':list(excluded_ops)}
            self._source('opening.'+account.lower(),source_payload)
            stmtid=self.rid('statement',account.lower())
            db.execute("INSERT INTO rec_statement_headers VALUES(?,?,?,?,NULL,NULL,?,'TRADE_DATE','VALIDATED',?)",(
                stmtid,account_id,self.artifact_id,self.prefix+'.'+account,cutoff,self.now()))
            anchorid=self.rid('anchor',account.lower())
            manifest=json.dumps({'included':[], 'excluded':list(excluded_ops),
                'unresolved':[] if certified else ['fixture-boundary']},sort_keys=True,separators=(',',':'))
            db.execute("INSERT INTO led_opening_anchors VALUES(?,?,?,?,?,1,?,?,'DRAFT',?,NULL,NULL)",(
                anchorid,account_id,stmtid,cutoff,'TRADE_DATE',manifest,digest(manifest),self.now()))
            for ccy,amount in cash.items():
                if ccy not in A0_CURRENCIES:
                    raise Rejected('unsupported synthetic cash currency')
                monetary_amount=dec(amount)
                if monetary_amount!=quantize_cent(monetary_amount):
                    raise Rejected('opening settled cash exceeds synthetic cent quantum')
                amount=canonical(monetary_amount)
                db.execute("INSERT INTO rec_statement_cash_balances VALUES(?,?,?,?,?,?,?)",(
                    self.rid('statement-cash',account.lower()+'.'+ccy.lower()),stmtid,ccy,'SETTLED',amount,account+'-'+ccy,self.now()))
                db.execute("INSERT INTO led_opening_cash VALUES(?,?,?,?,?,?,?)",(
                    self.rid('opening-cash',account.lower()+'.'+ccy.lower()),anchorid,ccy,'SETTLED',amount,account+'-'+ccy,self.now()))
            for i,p in enumerate(positions):
                inst=p.get('instrument','EQ1'); inst_id=self.iid(inst)
                qty=dec(p['qty'],scale=12,positive=True)
                cost=None if p.get('cost') is None else dec(p['cost'],nonnegative=True)
                coststat='UNKNOWN' if cost is None else 'KNOWN'
                posid=self.rid('opening-position',account.lower()+'.'+inst.lower()+'.'+str(i))
                db.execute('INSERT INTO rec_statement_positions VALUES(?,?,?,?,?,?,?,?,?,?,?)',(
                    self.rid('statement-position',account.lower()+'.'+str(i)),stmtid,inst_id,canonical(qty),coststat,
                    canonical(cost) if cost is not None else None,'EUR' if cost is not None else None,'position-'+str(i),None,None,self.now()))
                db.execute('INSERT INTO led_opening_positions VALUES(?,?,?,?,?,?,?,?,?,?)',(
                    posid,anchorid,inst_id,canonical(qty),coststat,canonical(cost) if cost is not None else None,
                    canonical(qty) if cost is not None else None,'EUR' if cost is not None else None,'position-'+str(i),self.now()))
                lots=p.get('lots')
                if lots is None: lots=[{'qty':p['qty'],'cost':p.get('cost')}]
                qsum=sum((dec(x['qty'],scale=12,positive=True) for x in lots),Decimal(0))
                if qsum!=qty: raise Rejected('opening lots do not partition opening position')
                for j,l in enumerate(lots):
                    lc=None if l.get('cost') is None else dec(l['cost'],nonnegative=True)
                    lq=dec(l['qty'],scale=12,positive=True)
                    lotid=self.rid('lot',account.lower()+'.opening.'+str(i)+'.'+str(j))
                    db.execute("INSERT INTO led_position_lots VALUES(?,?,?,'OPENING_POSITION',NULL,?,?,?,?,?,?,?,?,?)",(
                        lotid,account_id,inst_id,posid,canonical(lq),'UNKNOWN' if lc is None else 'KNOWN',
                        canonical(lc) if lc is not None else None,canonical(lq) if lc is not None else None,
                        'EUR' if lc is not None else None,cutoff,posid+'.'+f'{j:04d}',self.now()))
                if (cost is not None and any(x.get('cost') is None for x in lots)) or (cost is None and any(x.get('cost') is not None for x in lots)):
                    raise Rejected('partial historical cost requires a separately approved opening contract')
                if cost is not None and sum(dec(x['cost']) for x in lots)!=cost:
                    raise Rejected('opening lot costs do not reconcile')
            for i,p in enumerate(pending):
                typ=p['type']; ccy=p.get('currency','EUR'); amt=dec(p['amount'],positive=True)
                if ccy not in A0_CURRENCIES:raise Rejected('unsupported synthetic pending currency')
                if amt!=quantize_cent(amt):raise Rejected('opening obligation exceeds synthetic cent quantum')
                if typ not in ('PAYABLE','RECEIVABLE'):raise Rejected('invalid obligation type')
                pid=self.rid('opening-pending',account.lower()+'.'+str(i))
                db.execute('INSERT INTO led_opening_pending_items VALUES(?,?,?,?,?,?,?,?,?,?)',(
                    pid,anchorid,'pending-'+str(i),typ,ccy,canonical(amt),p.get('prior_execution'),None,'VERIFIED',self.now()))
                oid=self.rid('obligation',account.lower()+'.opening.'+str(i))
                db.execute("INSERT INTO led_settlement_obligations VALUES(?,'OPENING_ORIGIN',NULL,?,?,?,?,?, ?,NULL,'VERIFIED','CURRENT',NULL,?,?)",(
                    oid,pid,account_id,ccy,typ,canonical(amt),cutoff,pid,self.now()))
            recid=self.rid('reconciliation',account.lower())
            aid=self._audit(('certify-' if certified else 'block-')+'opening.'+account.lower(),'led_opening_anchors',anchorid,'ACCEPTED' if certified else 'BLOCKED')
            db.execute("INSERT INTO rec_reconciliation_runs VALUES(?,?,?,?,?,?,'TRADE_DATE','A0-v1',?,?,?,?,?,?)",(
                recid,account_id,stmtid,anchorid,'OPENING',cutoff,digest(source_payload),
                'PASSED' if certified else 'BLOCKED',self.now(),self.now(),aid,'synthetic opening reconciliation'))
            if certified:
                db.execute("UPDATE led_opening_anchors SET anchor_status='CERTIFIED',certification_audit_id=? WHERE opening_anchor_id=?",(aid,anchorid))
            else:
                db.execute("UPDATE led_opening_anchors SET anchor_status='BLOCKED' WHERE opening_anchor_id=?",(anchorid,))
                db.execute("INSERT INTO rec_reconciliation_breaks VALUES(?,?,1,'EXACT_CUTOFF_UNRESOLVED','OPENING_ANCHOR',?,'TIMING',NULL,NULL,NULL,NULL,NULL,'HIGH','OPEN',NULL,?,NULL)",(
                    self.rid('reconciliation-break',account.lower()+'.cutoff'),recid,anchorid,self.now()))
            db.commit()
            return anchorid
        except:
            db.rollback();raise

    def _validate_event(self,eid):
        """Fail-closed posting-class/sign/amount checks before financial publication."""
        event=self.one('SELECT * FROM led_financial_events WHERE event_id=?',(eid,))
        subtype=event['event_subtype'];terms=json.loads(event['event_terms_json'])
        posts=self.all('SELECT * FROM led_event_postings WHERE event_id=? ORDER BY posting_sequence',(eid,))
        if not posts:raise Rejected('no financial postings')
        classes={}
        for p in posts:
            cls=p['posting_class']
            val=dec(p['quantity_dec'],scale=12) if cls=='SECURITY_QTY' else dec(p['amount_dec'])
            classes.setdefault(cls,[]).append(val)
        def only(cls):return classes.get(cls,[])
        def single(cls):
            v=only(cls)
            if len(v)!=1:raise Rejected(f'{subtype}: expected one {cls}; got {len(v)}')
            return v[0]
        def no_extra(allowed):
            if not set(classes)<=set(allowed):raise Rejected(f'{subtype}: unsupported posting class')
        if subtype=='BUY':
            no_extra(('SECURITY_QTY','PAYABLE','FEE_MEMO','TAX_MEMO'))
            if single('SECURITY_QTY')!=dec(terms['qty'],scale=12) or single('PAYABLE')!=dec(terms['net']):raise Rejected('BUY posting mismatch')
            if sum(only('FEE_MEMO'),Decimal(0))!=dec(terms['fee']) or sum(only('TAX_MEMO'),Decimal(0))!=dec(terms['tax']):raise Rejected('BUY memo mismatch')
        elif subtype=='SELL':
            no_extra(('SECURITY_QTY','RECEIVABLE','FEE_MEMO','TAX_MEMO'))
            if sum(only('SECURITY_QTY'),Decimal(0))!=-dec(terms['qty'],scale=12) or any(v>=0 for v in only('SECURITY_QTY')) or single('RECEIVABLE')!=dec(terms['net']):raise Rejected('SELL posting mismatch')
            if sum(only('FEE_MEMO'),Decimal(0))!=dec(terms['fee']) or sum(only('TAX_MEMO'),Decimal(0))!=dec(terms['tax']):raise Rejected('SELL memo mismatch')
            allocations=self.all("SELECT allocated_quantity_dec FROM led_lot_allocations WHERE movement_event_id=? AND allocation_action='DISPOSE'",(eid,))
            if sum((dec(a['allocated_quantity_dec'],scale=12) for a in allocations),Decimal(0))!=dec(terms['qty'],scale=12):raise Rejected('sale lot allocation mismatch')
        elif subtype=='SETTLE_PAYABLE':
            no_extra(('SETTLED_CASH','PAYABLE'))
            if single('SETTLED_CASH')!=-dec(terms['amount']) or single('PAYABLE')!=-dec(terms['amount']):raise Rejected('payable settlement mismatch')
        elif subtype=='SETTLE_RECEIVABLE':
            no_extra(('SETTLED_CASH','RECEIVABLE'))
            if single('SETTLED_CASH')!=dec(terms['amount']) or single('RECEIVABLE')!=-dec(terms['amount']):raise Rejected('receivable settlement mismatch')
        elif subtype in ('CANCEL_UNSETTLED_PAYABLE','CANCEL_UNSETTLED_RECEIVABLE'):
            cls='PAYABLE' if subtype.endswith('PAYABLE') else 'RECEIVABLE'
            no_extra((cls,))
            if single(cls)!=-dec(terms['amount']):raise Rejected('obligation cancellation mismatch')
        elif subtype in ('DIVIDEND','INTEREST'):
            no_extra(('SETTLED_CASH','FEE_MEMO','TAX_MEMO'))
            if single('SETTLED_CASH')!=dec(terms['net']):raise Rejected('cash income mismatch')
            if sum(only('FEE_MEMO'),Decimal(0))!=dec(terms['fee']) or sum(only('TAX_MEMO'),Decimal(0))!=dec(terms['tax']):raise Rejected('cash income memo mismatch')
        elif subtype in ('STANDALONE_FEE','STANDALONE_TAX'):
            memo='FEE_MEMO' if subtype=='STANDALONE_FEE' else 'TAX_MEMO'
            no_extra(('SETTLED_CASH',memo))
            if single('SETTLED_CASH')!=-dec(terms['amount']) or single(memo)!=dec(terms['amount']):raise Rejected('standalone charge mismatch')
        elif subtype in ('EXTERNAL_DEPOSIT','EXTERNAL_WITHDRAWAL','TRANSFER_IN','TRANSFER_OUT','CASH_CORRECTION'):
            no_extra(('SETTLED_CASH',))
            if single('SETTLED_CASH')!=dec(terms['amount']):raise Rejected('cash flow mismatch')
        elif subtype in ('SPLIT','REVERSE_SPLIT'):
            no_extra(('SECURITY_QTY',))
            if subtype=='SPLIT' and any(v<=0 for v in only('SECURITY_QTY')):raise Rejected('split direction')
            if subtype=='REVERSE_SPLIT' and any(v>=0 for v in only('SECURITY_QTY')):raise Rejected('reverse split direction')
        elif subtype=='FX_CONVERSION':
            no_extra(('SETTLED_CASH','FEE_MEMO'))
            if len(only('SETTLED_CASH'))<2:raise Rejected('incomplete conversion')
        elif subtype in ('REVERSE_SETTLED_BUY','REVERSE_SETTLED_SELL'):
            allowed=('SECURITY_QTY','RECEIVABLE','FEE_MEMO') if subtype.endswith('BUY') else ('SECURITY_QTY','PAYABLE','FEE_MEMO')
            no_extra(allowed)
            if not only('SECURITY_QTY') or (sum(only('SECURITY_QTY'),Decimal(0))>=0 if subtype.endswith('BUY') else sum(only('SECURITY_QTY'),Decimal(0))<=0):
                raise Rejected('reverse security quantity')
        else:raise Rejected('template unsupported: '+subtype)
        if len(posts)!=sum(map(len,classes.values())):raise Rejected('unexpected posting count')

    def _event(self,op,account,subtype,terms,build,instrument=None,corrects=None,when=None):
        if subtype not in EAT_TYPES:raise Rejected('unsupported financial event')
        db=self.db; aid=self.aid(account); iid=self.iid(instrument) if instrument else None
        if when is None:
            old_time=self.one('SELECT economic_effective_at FROM led_financial_events WHERE source_id=? AND account_id=? AND provider_operation_scope=? AND provider_operation_key=? AND event_subtype=?',(
                self.source_id,aid,'A0_SYNTHETIC',op,subtype))
            when=old_time['economic_effective_at'] if old_time else self.t(self._op_no+1)
        when=validate_utc_seconds(when)
        anchor=self.one('SELECT cutoff_at,anchor_status,included_effects_manifest_json FROM led_opening_anchors WHERE account_id=?',(aid,))
        if anchor is None:
            raise Rejected('certified synthetic opening anchor required before financial event')
        if anchor:
            if anchor['anchor_status']!='CERTIFIED':
                raise Rejected('account opening anchor is not certified')
            if when<anchor['cutoff_at']:
                raise Rejected('event predates opening anchor; economic effect may already be included')
            if when==anchor['cutoff_at']:
                excluded=json.loads(anchor['included_effects_manifest_json']).get('excluded',[])
                if op not in excluded:
                    raise Rejected('exact-cutoff event requires source-supported exclusion in opening anchor')
        template=EAT_TYPES[subtype]; tid=self.sid('template',template.lower())
        payload={'op':op,'account':account,'subtype':subtype,'terms':terms,'instrument':instrument,'corrects':corrects,'when':when}
        event_hash=digest(payload)
        previous_op_no=self._op_no
        db.execute('BEGIN IMMEDIATE')
        try:
            old=self.one('SELECT event_id,event_content_sha256 FROM led_financial_events WHERE source_id=? AND account_id=? AND provider_operation_scope=? AND provider_operation_key=? AND event_subtype=?',(
                self.source_id,aid,'A0_SYNTHETIC',op,subtype))
            if old:
                klass='DUPLICATE' if old['event_content_sha256']==event_hash else 'QUARANTINED'
                self._source(op,payload,klass)
                self._audit('reimport.'+op,'led_financial_events',old['event_id'],'REJECTED')
                db.commit()
                if klass=='QUARANTINED':raise Conflict('same source identity, conflicting economic terms')
                return old['event_id']
            eid=self.rid('event',op)
            if self.one('SELECT 1 FROM led_financial_events WHERE event_id=?',(eid,)):
                raise Conflict('operation alias already used in another economic scope; use a unique fixture operation alias')
            src=self._source(op,payload)
            db.execute("INSERT INTO led_financial_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'STAGED',?,?,?,?,?,?,?,?,?,?,?)",(
                eid,aid,self.source_id,iid,tid,'FINANCIAL',subtype,op,'A0_SYNTHETIC',None,when,when,self.now(),
                self.rid('event',corrects) if corrects else None,event_hash,'A0-EVENT-v1',
                json.dumps(terms,sort_keys=True,separators=(',',':')),None,None,None,None,None,None,None))
            self._op_no+=1
            build(eid,aid,iid,tid,when)
            linkid=self.rid('source-link',op)
            db.execute("INSERT INTO src_record_event_links VALUES(?,?,?,'EVIDENCES','VERIFIED',?)",(linkid,src,eid,self.now()))
            self._validate_event(eid)
            self._audit('accepted.'+op,'led_financial_events',eid)
            db.execute("UPDATE led_financial_events SET event_status='ACCEPTED' WHERE event_id=?",(eid,))
            db.commit()
            return eid
        except:
            if db.in_transaction:db.rollback()
            self._op_no=previous_op_no
            raise

    def _post(self,eid,aid,tid,when,klass,value,ccy='EUR',iid=None,lot=None,obligation=None):
        num=self.one('SELECT COALESCE(MAX(posting_sequence),0)+1 n FROM led_event_postings WHERE event_id=?',(eid,))['n']
        pid=self.rid('posting',eid+'.'+str(num))
        isqty=klass=='SECURITY_QTY'
        if isqty:
            q=canonical(dec(value,scale=12)); v=None; ccy=None; typ=None
            if q=='0':raise Rejected('zero security posting')
        else:
            posted_amount=dec(value)
            if ccy not in A0_CURRENCIES:raise Rejected('unsupported synthetic posting currency')
            if posted_amount!=quantize_cent(posted_amount):
                raise Rejected('monetary posting exceeds synthetic cent quantum')
            v=canonical(posted_amount);q=None;lot=None
            if v=='0':raise Rejected('zero monetary posting')
            typ=klass if klass in ('PAYABLE','RECEIVABLE') else None
        self.db.execute('INSERT INTO led_event_postings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(
            pid,eid,aid,tid,num,klass,iid if isqty else None,lot,obligation,typ,ccy,v,q,when,self.now(),eid))
        return pid

    def _lot(self,eid,aid,iid,when,qty,cost,alias):
        l_id=self.rid('lot',alias)
        cost=dec(cost,nonnegative=True) if cost is not None else None
        self.db.execute("INSERT INTO led_position_lots VALUES(?,?,?,'ACQUISITION_EVENT',?,NULL,?,?,?,?,?,?,?,?)",(
            l_id,aid,iid,eid,canonical(dec(qty,scale=12,positive=True)),
            'KNOWN' if cost is not None else 'UNKNOWN',canonical(cost) if cost is not None else None,
            canonical(dec(qty,scale=12)) if cost is not None else None,
            'EUR' if cost is not None else None,when,eid,self.now()))
        return l_id

    def _obligation(self,eid,aid,ccy,typ,value,alias):
        oid=self.rid('obligation',alias)
        self.db.execute("INSERT INTO led_settlement_obligations VALUES(?,'EVENT_ORIGIN',?,NULL,?,?,?,?, ?,NULL,'VERIFIED','CURRENT',NULL,?,?)",(
            oid,eid,aid,ccy,typ,canonical(dec(value,positive=True)),self.t(self._op_no),eid,self.now()))
        return oid

    def _alloc(self,lot,eid,action,qty,cost,alias,reverse=None):
        if action not in ('DISPOSE','RESTORE','VOID_ACQUISITION'):raise Rejected('unknown lot action')
        self.db.execute('INSERT INTO led_lot_allocations VALUES(?,?,?,?,?,?,?,?,?,?,?)',(
            self.rid('allocation',alias),lot,eid,action,quantity(qty,positive=True),
            canonical(dec(cost,nonnegative=True)) if cost is not None else None,
            'KNOWN' if cost is not None else 'UNKNOWN',reverse,'FIFO' if action=='DISPOSE' else None,
            eid,self.now()))
        return self.rid('allocation',alias)

    def _relation(self,left,right,kind,alias):
        lid=self.rid('event',left) if not str(left).count('-')==4 else left
        rid=self.rid('event',right) if not str(right).count('-')==4 else right
        self.db.execute("INSERT INTO led_event_relations VALUES(?,?,?,?, 'A0-v1',NULL,'ACCEPTED',?,?)",(
            self.rid('relation',alias),lid,rid,kind,self.now(),self.now()))

    def buy(self,op,account='BRK',instrument='EQ1',qty='10',price='100',fee='0',tax='0',when=None):
        q=dec(qty,scale=12,positive=True); p=dec(price,scale=12,nonnegative=True)
        f=dec(fee,nonnegative=True);t=dec(tax,nonnegative=True)
        g=gross(canonical(q),canonical(p));net=g+f+t
        terms={'qty':canonical(q),'price':canonical(p),'gross':canonical(g),'fee':canonical(f),'tax':canonical(t),'net':canonical(net)}
        def build(eid,aid,iid,tid,wt):
            lot=self._lot(eid,aid,iid,wt,q,net,op)
            obl=self._obligation(eid,aid,'EUR','PAYABLE',net,op)
            self._post(eid,aid,tid,wt,'SECURITY_QTY',q,iid=iid,lot=lot)
            self._post(eid,aid,tid,wt,'PAYABLE',net,obligation=obl)
            if f:self._post(eid,aid,tid,wt,'FEE_MEMO',f)
            if t:self._post(eid,aid,tid,wt,'TAX_MEMO',t)
        return self._event(op,account,'BUY',terms,build,instrument,when=when)

    def obligation(self,account='BRK',typ='PAYABLE',index=0):
        aid=self.aid(account)
        # Position-based selection is stable by economic recognition, not filesystem/clock
        # resolution.  Windows can assign identical recorded_at values to two buys;
        # UUID lexical order is unrelated to their obligation creation sequence.
        rows=self.all('SELECT * FROM led_settlement_obligations WHERE account_id=? AND obligation_type=? ORDER BY recognized_at, source_evidence_reference, settlement_obligation_id',(aid,typ))
        return rows[index]['settlement_obligation_id']

    def progress(self,oid):
        row=self.one('SELECT * FROM led_settlement_obligations WHERE settlement_obligation_id=?',(oid,))
        if not row:raise Rejected('unknown obligation')
        o=dec(row['original_amount_dec']);s=Decimal(0);c=Decimal(0)
        records=self.all("SELECT p.amount_dec,e.event_subtype FROM led_event_postings p JOIN led_financial_events e USING(event_id) WHERE p.settlement_obligation_id=? AND e.event_status='ACCEPTED'",(oid,))
        for r in records:
            v=dec(r['amount_dec'])
            if v<0 and r['event_subtype'].startswith('SETTLE_'):s-=v
            elif v<0 and r['event_subtype'].startswith('CANCEL_'):c-=v
        u=o-s-c
        if u<0:raise AssertionError('negative outstanding')
        if u>0:state='OPEN_PARTIAL' if s+c>0 else 'OPEN'
        elif s==o:state='SETTLED_FULL'
        elif c==o:state='CANCELLED_FULL'
        else:state='CLOSED_MIXED'
        return {'O':o,'S':s,'C':c,'U':u,'state':state,'evidence_status':row['evidence_status']}

    def settle(self,op,oid,amount,when=None):
        amt=dec(amount,positive=True)
        row=self.one('SELECT * FROM led_settlement_obligations WHERE settlement_obligation_id=?',(oid,))
        if row is None:raise Rejected('unknown obligation')
        subtype='SETTLE_'+row['obligation_type'];account=self.one('SELECT account_key FROM ref_accounts WHERE account_id=?',(row['account_id'],))['account_key']
        def build(eid,aid,iid,tid,wt):
            curr=self.progress(oid)
            if amt>curr['U']:raise Rejected('settlement exceeds outstanding')
            ccy=row['currency_code'];typ=row['obligation_type']
            self._post(eid,aid,tid,wt,'SETTLED_CASH',-amt if typ=='PAYABLE' else amt,ccy=ccy)
            self._post(eid,aid,tid,wt,typ,-amt,ccy=ccy,obligation=oid)
            if row['originating_event_id']:
                self._relation(eid,row['originating_event_id'],'SETTLES',op+'.settles')
        return self._event(op,account,subtype,{'obligation':oid,'amount':canonical(amt)},build,when=when)

    def cancel(self,op,oid,amount,when=None):
        amt=dec(amount,positive=True)
        row=self.one('SELECT * FROM led_settlement_obligations WHERE settlement_obligation_id=?',(oid,))
        if not row:raise Rejected('unknown obligation')
        subtype='CANCEL_UNSETTLED_'+row['obligation_type']
        account=self.one('SELECT account_key FROM ref_accounts WHERE account_id=?',(row['account_id'],))['account_key']
        if row['originating_event_id']:
            original=self.one('SELECT event_subtype FROM led_financial_events WHERE event_id=?',(row['originating_event_id'],))
            if original['event_subtype'] in ('BUY','SELL'):
                raise Rejected('securities-linked cancellation requires quantity/cost correction')
        elif row['opening_pending_id']:
            openrow=self.one('SELECT prior_execution_reference FROM led_opening_pending_items WHERE opening_pending_id=?',(row['opening_pending_id'],))
            if openrow['prior_execution_reference'] and openrow['prior_execution_reference'].startswith('TRADE:'):
                raise Rejected('trade-linked pending cancellation requires security correction')
        def build(eid,aid,iid,tid,wt):
            if amt>self.progress(oid)['U']:raise Rejected('cancellation exceeds outstanding')
            self._post(eid,aid,tid,wt,row['obligation_type'],-amt,ccy=row['currency_code'],obligation=oid)
        return self._event(op,account,subtype,{'obligation':oid,'amount':canonical(amt)},build,when=when)

    def lots(self,account='BRK',instrument='EQ1'):
        # Stable source-derived FIFO tie-break (opening lot ordinal in origin evidence),
        # never runtime recorded_at or arbitrary SQLite row insertion order.
        return self.all('SELECT * FROM led_position_lots WHERE account_id=? AND instrument_id=? ORDER BY origin_effective_at,origin_evidence_reference,lot_id',(self.aid(account),self.iid(instrument)))
    def lot_qty(self,lid):
        row=self.one('SELECT * FROM led_position_lots WHERE lot_id=?',(lid,))
        if not row:raise Rejected('unknown lot')
        qty=dec(row['initial_quantity_dec'],scale=12) if row['lot_origin_type']=='OPENING_POSITION' else Decimal(0)
        for p in self.all("SELECT quantity_dec FROM led_event_postings p JOIN led_financial_events e USING(event_id) WHERE p.lot_id=? AND p.posting_class='SECURITY_QTY' AND e.event_status='ACCEPTED'",(lid,)):
            qty+=dec(p['quantity_dec'],scale=12)
        return qty
    def lot_cost(self,lid):
        row=self.one('SELECT * FROM led_position_lots WHERE lot_id=?',(lid,))
        if row['cost_basis_status']!='KNOWN':return None
        cost=dec(row['verified_initial_cost_dec'])
        for a in self.all("SELECT a.allocation_action,a.allocated_cost_dec FROM led_lot_allocations a JOIN led_financial_events e ON e.event_id=a.movement_event_id WHERE a.lot_id=? AND e.event_status='ACCEPTED'",(lid,)):
            if a['allocated_cost_dec'] is None:return None
            v=dec(a['allocated_cost_dec']);cost+= v if a['allocation_action']=='RESTORE' else -v
        return cost

    def sell(self,op,account='BRK',instrument='EQ1',qty='5',price='120',fee='0',tax='0',when=None):
        q=dec(qty,scale=12,positive=True);p=dec(price,scale=12,nonnegative=True);f=dec(fee,nonnegative=True);t=dec(tax,nonnegative=True)
        net=gross(canonical(q),canonical(p))-f-t
        if net<=0:raise Rejected('invalid sale net consideration')
        terms={'qty':canonical(q),'price':canonical(p),'fee':canonical(f),'tax':canonical(t),'net':canonical(net)}
        def build(eid,aid,iid,tid,wt):
            left=q; allocations=[]
            for lot in self.lots(account,instrument):
                available=self.lot_qty(lot['lot_id'])
                if available<=0:continue
                use=min(left,available)
                cost=self.lot_cost(lot['lot_id'])
                alloc_cost=None if cost is None else (cost if use==available else quantize_cent(cost*use/available))
                allocations.append((lot['lot_id'],use,alloc_cost))
                left-=use
                if left==0:break
            if left>0:raise Rejected('sell exceeds accepted holdings')
            obl=self._obligation(eid,aid,'EUR','RECEIVABLE',net,op)
            for i,(lot,use,cost) in enumerate(allocations):
                self._post(eid,aid,tid,wt,'SECURITY_QTY',-use,iid=iid,lot=lot)
                self._alloc(lot,eid,'DISPOSE',use,cost,op+'.'+str(i))
            self._post(eid,aid,tid,wt,'RECEIVABLE',net,obligation=obl)
            if f:self._post(eid,aid,tid,wt,'FEE_MEMO',f)
            if t:self._post(eid,aid,tid,wt,'TAX_MEMO',t)
        return self._event(op,account,'SELL',terms,build,instrument,when=when)

    def split(self,op,ratio_num,ratio_den,account='BRK',instrument='EQ1',when=None):
        n=dec(ratio_num,positive=True);d=dec(ratio_den,positive=True)
        terms={'numerator':canonical(n),'denominator':canonical(d)}
        subtype='SPLIT' if n>d else 'REVERSE_SPLIT'
        def build(eid,aid,iid,tid,wt):
            total=Decimal(0)
            for lot in self.lots(account,instrument):
                before=self.lot_qty(lot['lot_id'])
                if before<=0:continue
                after=before*n/d
                if after!=after.to_integral_value():raise Rejected('fractional splits outside A0 scope')
                delta=after-before
                if delta:
                    self._post(eid,aid,tid,wt,'SECURITY_QTY',delta,iid=iid,lot=lot['lot_id']);total+=delta
            if total==0:raise Rejected('no affected holdings')
        return self._event(op,account,subtype,terms,build,instrument,when=when)

    def income(self,op,kind='DIVIDEND',account='BRK',gross_amount='20',tax='0',fee='0',when=None):
        if kind not in ('DIVIDEND','INTEREST'):raise Rejected('invalid cash income kind')
        g=dec(gross_amount,nonnegative=True); t=dec(tax,nonnegative=True); f=dec(fee,nonnegative=True)
        net=g-t-f
        if net<=0:raise Rejected('income net must be positive in A0 template')
        def build(eid,aid,iid,tid,wt):
            self._post(eid,aid,tid,wt,'SETTLED_CASH',net)
            if t:self._post(eid,aid,tid,wt,'TAX_MEMO',t)
            if f:self._post(eid,aid,tid,wt,'FEE_MEMO',f)
        return self._event(op,account,kind,{'gross':cstr(g),'tax':cstr(t),'fee':cstr(f),'net':cstr(net)},build,when=when)

    def charge(self,op,kind='STANDALONE_FEE',amount='2',account='BRK',when=None):
        if kind not in ('STANDALONE_FEE','STANDALONE_TAX'):raise Rejected('invalid charge kind')
        v=dec(amount,positive=True)
        def build(eid,aid,iid,tid,wt):
            self._post(eid,aid,tid,wt,'SETTLED_CASH',-v)
            self._post(eid,aid,tid,wt,'FEE_MEMO' if kind=='STANDALONE_FEE' else 'TAX_MEMO',v)
        return self._event(op,account,kind,{'amount':canonical(v)},build,when=when)

    def cashflow(self,op,amount,account='BRK',kind='EXTERNAL_DEPOSIT',when=None):
        if kind not in ('EXTERNAL_DEPOSIT','EXTERNAL_WITHDRAWAL','TRANSFER_OUT','TRANSFER_IN','CASH_CORRECTION'):
            raise Rejected('invalid cashflow kind')
        v=dec(amount)
        if v==0:raise Rejected('zero cashflow')
        if kind in ('EXTERNAL_DEPOSIT','TRANSFER_IN') and v<0:raise Rejected('incoming flow must be positive')
        if kind in ('EXTERNAL_WITHDRAWAL','TRANSFER_OUT') and v>0:raise Rejected('outgoing flow must be negative')
        def build(eid,aid,iid,tid,wt):self._post(eid,aid,tid,wt,'SETTLED_CASH',v)
        return self._event(op,account,kind,{'amount':canonical(v)},build,when=when)

    def match_transfer(self,out_op,in_op):
        left=self.one('SELECT * FROM led_financial_events WHERE event_id=?',(self.rid('event',out_op),))
        right=self.one('SELECT * FROM led_financial_events WHERE event_id=?',(self.rid('event',in_op),))
        if not left or not right or left['event_subtype']!='TRANSFER_OUT' or right['event_subtype']!='TRANSFER_IN':
            raise Rejected('invalid transfer leg pair')
        if left['account_id']==right['account_id']:raise Rejected('same-account transfer match')
        def amt(eid):
            p=self.one("SELECT amount_dec,currency_code FROM led_event_postings WHERE event_id=? AND posting_class='SETTLED_CASH'",(eid,))
            return dec(p['amount_dec']),p['currency_code']
        a,ac=amt(left['event_id']);b,bc=amt(right['event_id'])
        if a+b!=0 or ac!=bc:raise Rejected('different principal or currency')
        if self.one("SELECT 1 FROM led_event_relations WHERE relation_type='TRANSFER_MATCH' AND relation_status='ACCEPTED' AND (from_event_id IN (?,?) OR to_event_id IN (?,?))",(left['event_id'],right['event_id'],left['event_id'],right['event_id'])):
            raise Rejected('transfer leg already matched')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._relation(out_op,in_op,'TRANSFER_MATCH',out_op+'.'+in_op)
            self.db.commit()
        except:
            self.db.rollback();raise

    def fx(self,op,account='BRK',sold='EUR',sold_amount='90',bought='USD',bought_amount='100',fee='1',fee_ccy='EUR',when=None):
        s=dec(sold_amount,positive=True);b=dec(bought_amount,positive=True);f=dec(fee,nonnegative=True)
        if sold==bought:raise Rejected('FX same-currency transfer invalid')
        if sold not in A0_CURRENCIES or bought not in A0_CURRENCIES or fee_ccy not in A0_CURRENCIES:
            raise Rejected('unsupported synthetic FX currency')
        def build(eid,aid,iid,tid,wt):
            self._post(eid,aid,tid,wt,'SETTLED_CASH',-s,ccy=sold)
            self._post(eid,aid,tid,wt,'SETTLED_CASH',b,ccy=bought)
            if f:
                self._post(eid,aid,tid,wt,'SETTLED_CASH',-f,ccy=fee_ccy)
                self._post(eid,aid,tid,wt,'FEE_MEMO',f,ccy=fee_ccy)
        terms={'sold':sold,'sold_amount':cstr(s),'bought':bought,'bought_amount':cstr(b),'fee':cstr(f),'fee_currency':fee_ccy}
        return self._event(op,account,'FX_CONVERSION',terms,build,when=when)

    def reverse_buy(self,op,original_op,refund,when=None):
        orig=self.one('SELECT * FROM led_financial_events WHERE event_id=?',(self.rid('event',original_op),))
        if not orig or orig['event_subtype']!='BUY':raise Rejected('not a supported purchase')
        original_obl=self.one('SELECT settlement_obligation_id FROM led_settlement_obligations WHERE originating_event_id=?',(orig['event_id'],))
        if self.progress(original_obl['settlement_obligation_id'])['state']!='SETTLED_FULL':raise Rejected('purchase not fully settled')
        lot=self.one('SELECT * FROM led_position_lots WHERE acquisition_event_id=?',(orig['event_id'],))
        avail=self.lot_qty(lot['lot_id']);original_qty=dec(lot['initial_quantity_dec'],scale=12)
        if avail!=original_qty:raise Rejected('partial acquisition reversal requires separate contract')
        initialcost=self.lot_cost(lot['lot_id']);v=dec(refund,positive=True)
        if initialcost is None or v!=initialcost:raise Rejected('fixture requires entire original cost refunded')
        origterms=json.loads(orig['event_terms_json']);fee=dec(origterms['fee'])
        account=self.one('SELECT account_key FROM ref_accounts WHERE account_id=?',(orig['account_id'],))['account_key']
        def build(eid,aid,iid,tid,wt):
            obl=self._obligation(eid,aid,'EUR','RECEIVABLE',v,op)
            self._post(eid,aid,tid,wt,'SECURITY_QTY',-original_qty,iid=iid,lot=lot['lot_id'])
            self._post(eid,aid,tid,wt,'RECEIVABLE',v,obligation=obl)
            if fee:self._post(eid,aid,tid,wt,'FEE_MEMO',-fee)
            self._alloc(lot['lot_id'],eid,'VOID_ACQUISITION',original_qty,initialcost,op+'.void')
            self._relation(eid,orig['event_id'],'CORRECTS',op+'.corrects')
        return self._event(op,account,'REVERSE_SETTLED_BUY',{'original':original_op,'refund':cstr(v)},build,'EQ1',corrects=original_op,when=when)

    def reverse_sell(self,op,original_op,repayment,when=None):
        orig=self.one('SELECT * FROM led_financial_events WHERE event_id=?',(self.rid('event',original_op),))
        if not orig or orig['event_subtype']!='SELL':raise Rejected('not supported sale')
        original_obl=self.one('SELECT settlement_obligation_id FROM led_settlement_obligations WHERE originating_event_id=?',(orig['event_id'],))
        if self.progress(original_obl['settlement_obligation_id'])['state']!='SETTLED_FULL':raise Rejected('sale not settled')
        allocs=self.all("SELECT * FROM led_lot_allocations WHERE movement_event_id=? AND allocation_action='DISPOSE'",(orig['event_id'],))
        v=dec(repayment,positive=True)
        terms=json.loads(orig['event_terms_json'])
        if v!=dec(terms['net']):raise Rejected('repayment must match full original proceeds')
        fee=dec(terms['fee'])
        account=self.one('SELECT account_key FROM ref_accounts WHERE account_id=?',(orig['account_id'],))['account_key']
        def build(eid,aid,iid,tid,wt):
            # Enforced within BEGIN IMMEDIATE: a second independently keyed correction
            # cannot reinstate the same disposed securities twice.
            if self.one("SELECT 1 FROM led_financial_events WHERE correction_of_event_id=? AND event_subtype='REVERSE_SETTLED_SELL' AND event_status='ACCEPTED'",(orig['event_id'],)):
                raise Rejected('sale already fully reversed')
            obl=self._obligation(eid,aid,'EUR','PAYABLE',v,op)
            for i,a in enumerate(allocs):
                qty=dec(a['allocated_quantity_dec'],scale=12)
                self._post(eid,aid,tid,wt,'SECURITY_QTY',qty,iid=iid,lot=a['lot_id'])
                self._alloc(a['lot_id'],eid,'RESTORE',qty,a['allocated_cost_dec'],op+'.'+str(i),a['lot_allocation_id'])
            self._post(eid,aid,tid,wt,'PAYABLE',v,obligation=obl)
            if fee:self._post(eid,aid,tid,wt,'FEE_MEMO',-fee)
            self._relation(eid,orig['event_id'],'CORRECTS',op+'.corrects')
        return self._event(op,account,'REVERSE_SETTLED_SELL',{'original':original_op,'repayment':cstr(v)},build,'EQ1',corrects=original_op,when=when)

    def realized_gain(self,account='BRK'):
        total=Decimal(0)
        for e in self.all("SELECT * FROM led_financial_events WHERE account_id=? AND event_subtype='SELL' AND event_status='ACCEPTED'",(self.aid(account),)):
            # Fully reversed sales are neutral in the current derived P&L.
            reversed_sale=self.one("SELECT 1 FROM led_financial_events WHERE correction_of_event_id=? AND event_subtype='REVERSE_SETTLED_SELL' AND event_status='ACCEPTED'",(e['event_id'],))
            if reversed_sale:continue
            allocs=self.all("SELECT allocated_cost_dec FROM led_lot_allocations WHERE movement_event_id=? AND allocation_action='DISPOSE'",(e['event_id'],))
            if any(a['allocated_cost_dec'] is None for a in allocs):return None
            cost=sum((dec(a['allocated_cost_dec']) for a in allocs),Decimal(0))
            terms=json.loads(e['event_terms_json'])
            total+=dec(terms['net'])-cost
        return total

    def balance_cash(self,account='BRK',currency='EUR',T=None,K=None):
        aid=self.aid(account)

        if T is not None:
            T=validate_utc_seconds(T)
            a=self.one('SELECT cutoff_at FROM led_opening_anchors WHERE account_id=?',(aid,))
            if a is not None and T<a['cutoff_at']:
                raise Rejected('requested cash snapshot predates available opening anchor')
        if K is not None:K=validate_utc_seconds(K)
        base=sum((dec(r['amount_dec']) for r in self.all("SELECT c.amount_dec FROM led_opening_cash c JOIN led_opening_anchors a USING(opening_anchor_id) WHERE a.account_id=? AND c.currency_code=? AND c.balance_type='SETTLED'",(aid,currency))),Decimal(0))
        cond="p.account_id=? AND p.posting_class='SETTLED_CASH' AND p.currency_code=? AND e.event_status='ACCEPTED'"
        args=[aid,currency]
        if T: cond+=' AND e.economic_effective_at<=?';args.append(T)
        if K: cond+=' AND e.available_at<=?';args.append(K)
        rows=self.all('SELECT p.amount_dec FROM led_event_postings p JOIN led_financial_events e USING(event_id) WHERE '+cond,args)
        return base+sum((dec(r['amount_dec']) for r in rows),Decimal(0))

    def qty(self,account='BRK',instrument='EQ1',T=None,K=None):
        aid=self.aid(account);iid=self.iid(instrument)
        if T is not None:
            T=validate_utc_seconds(T)
            a=self.one('SELECT cutoff_at FROM led_opening_anchors WHERE account_id=?',(aid,))
            if a is not None and T<a['cutoff_at']:
                raise Rejected('requested holdings snapshot predates available opening anchor')
        if K is not None:K=validate_utc_seconds(K)
        base=sum((dec(r['quantity_dec'],scale=12) for r in self.all('SELECT x.quantity_dec FROM led_opening_positions x JOIN led_opening_anchors a USING(opening_anchor_id) WHERE a.account_id=? AND x.instrument_id=?',(aid,iid))),Decimal(0))
        cond="p.account_id=? AND p.instrument_id=? AND p.posting_class='SECURITY_QTY' AND e.event_status='ACCEPTED'"
        args=[aid,iid]
        if T:cond+=' AND e.economic_effective_at<=?';args.append(T)
        if K:cond+=' AND e.available_at<=?';args.append(K)
        v=sum((dec(r['quantity_dec'],scale=12) for r in self.all('SELECT p.quantity_dec FROM led_event_postings p JOIN led_financial_events e USING(event_id) WHERE '+cond,args)),Decimal(0))
        return base+v

    def obligation_balance(self,account='BRK',typ='PAYABLE',currency='EUR'):
        aid=self.aid(account)
        ids=self.all('SELECT settlement_obligation_id FROM led_settlement_obligations WHERE account_id=? AND obligation_type=? AND currency_code=? AND record_version_status=\'CURRENT\'',(aid,typ,currency))
        return sum((self.progress(r['settlement_obligation_id'])['U'] for r in ids),Decimal(0))

    def transfer_certification_status(self,accounts=('BRK','BNK'),T=None,K=None):
        """In-flight account-local transfers must not yield a certified consolidated snapshot."""
        account_ids=[self.aid(a) for a in accounts]
        if not account_ids:return 'NOT_CERTIFIED'
        placeholders=','.join('?' for _ in account_ids)
        q=f"SELECT event_id FROM led_financial_events WHERE account_id IN ({placeholders}) AND event_status='ACCEPTED' AND event_subtype IN ('TRANSFER_OUT','TRANSFER_IN')"
        args=list(account_ids)
        if T:q+=' AND economic_effective_at<=?';args.append(T)
        if K:q+=' AND available_at<=?';args.append(K)
        legs=[r['event_id'] for r in self.all(q,args)]
        if not legs:return 'CERTIFIABLE_SYNTHETIC'
        active=set(legs)
        for eid in legs:
            matched=self.all("SELECT from_event_id,to_event_id FROM led_event_relations WHERE relation_type='TRANSFER_MATCH' AND relation_status='ACCEPTED' AND (from_event_id=? OR to_event_id=?)",(eid,eid))
            if not any(r['from_event_id'] in active and r['to_event_id'] in active for r in matched):
                return 'NOT_CERTIFIED_IN_FLIGHT_TRANSFER'
        return 'CERTIFIABLE_SYNTHETIC'

    def nav(self,accounts=('BRK',),price=None,fx=None):
        if len(accounts)!=len(set(accounts)):raise Rejected('duplicate account in consolidated NAV')
        for account in accounts:
            row=self.one('SELECT anchor_status FROM led_opening_anchors WHERE account_id=?',(self.aid(account),))
            if not row or row['anchor_status']!='CERTIFIED':raise Rejected('uncertified synthetic opening anchor')
        if self.transfer_certification_status(accounts)!='CERTIFIABLE_SYNTHETIC':
            raise Rejected('in-flight transfer prevents certified consolidated NAV')
        price={} if price is None else price
        fx={'EUR':'1'} if fx is None else fx
        total=Decimal(0)
        for account in accounts:
            currencies=set([r['currency_code'] for r in self.all('SELECT currency_code FROM led_opening_cash c JOIN led_opening_anchors a USING(opening_anchor_id) WHERE a.account_id=?',(self.aid(account),))])
            currencies.update(r['currency_code'] for r in self.all("SELECT DISTINCT currency_code FROM led_event_postings WHERE account_id=? AND currency_code IS NOT NULL",(self.aid(account),)))
            currencies.update(r['currency_code'] for r in self.all('SELECT DISTINCT currency_code FROM led_settlement_obligations WHERE account_id=?',(self.aid(account),)))
            for ccy in currencies:
                if ccy not in fx:raise Rejected('missing synthetic FX rate: '+ccy)
                rate=dec(fx[ccy],scale=12,positive=True)
                net=self.balance_cash(account,ccy)+self.obligation_balance(account,'RECEIVABLE',ccy)-self.obligation_balance(account,'PAYABLE',ccy)
                total+=net*rate
            for instrument in ('EQ1',):
                n=self.qty(account,instrument)
                if n:
                    if instrument not in price:raise Rejected('cannot value holdings without synthetic price')
                    total+=n*dec(price[instrument],scale=12,nonnegative=True)
        return total

    def audit_invariants(self,accounts=('BRK','BNK')):
        errors=[]
        if self.db.execute('PRAGMA foreign_key_check').fetchall():errors.append('FK violation')
        for account in accounts:
            try:
                recognized=self.qty(account)
                lots=sum((self.lot_qty(l['lot_id']) for l in self.lots(account)),Decimal(0))
                if recognized!=lots:errors.append(f'{account}: account quantity {recognized} != lots {lots}')
                if recognized<0:errors.append(f'{account}: negative long-only holdings')
                for l in self.lots(account):
                    if self.lot_qty(l['lot_id'])<0:errors.append('negative lot quantity')
                    cost=self.lot_cost(l['lot_id'])
                    if cost is not None and cost<0:errors.append('negative remaining lot cost')
            except Exception as exc:errors.append(str(exc))
        for r in self.all('SELECT settlement_obligation_id FROM led_settlement_obligations'):
            try:
                p=self.progress(r['settlement_obligation_id'])
                if p['O']!=p['S']+p['C']+p['U']:errors.append('obligation conservation')
            except Exception as exc:errors.append(str(exc))
        for r in self.all("SELECT event_id FROM led_financial_events WHERE event_status='ACCEPTED'"):
            if self.one("SELECT 1 FROM src_record_event_links WHERE event_id=? AND evidence_status='VERIFIED'",(r['event_id'],)) is None:
                errors.append('accepted event without source link')
        return errors
