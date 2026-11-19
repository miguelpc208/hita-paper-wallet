"""Versioned, transactional five-migration SQLite schema with recorded state fingerprints."""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import hashlib, sqlite3, uuid, json

MIGRATIONS = Path(__file__).resolve().parent.parent / 'migrations'

def connect(path=':memory:'):
    db = sqlite3.connect(str(path), isolation_level=None, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=5000')
    db.execute('PRAGMA journal_mode=WAL')
    return db

def schema_fingerprint(db):
    """Hash actual stored SQL definitions, not a guess based only on migration filename."""
    rows=[{'type':r['type'],'name':r['name'],'sql':r['sql']}
        for r in db.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type,name")]
    return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def apply_migrations(db):
    files=sorted(MIGRATIONS.glob('*.sql'))
    if len(files)!=5:
        raise AssertionError('Expected exactly five A0 migration files')
    applied=[]
    for index, migration in enumerate(files):
        version=migration.stem
        sql=migration.read_text(encoding='utf-8')
        digest=hashlib.sha256(sql.encode()).hexdigest()
        exists=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='gov_schema_migrations'").fetchone()
        if exists:
            row=db.execute('SELECT definition_sha256 FROM gov_schema_migrations WHERE migration_version=?',(version,)).fetchone()
            if row:
                if row['definition_sha256']!=digest:
                    raise ValueError('Migration checksum mismatch: '+version)
                applied.append(version)
                continue
        mid=str(uuid.uuid5(uuid.NAMESPACE_DNS,'hita.a0.migration.'+version))
        parent=files[index-1].stem if index else None
        # Transaction contains all DDL, observed resulting-schema fingerprint and migration record.
        try:
            db.executescript('BEGIN IMMEDIATE;\n'+sql+'\n')
            fp=schema_fingerprint(db)
            timestamp=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
            db.execute("INSERT INTO gov_schema_migrations(migration_id,migration_version,parent_version,definition_sha256,target_schema_sha256,applied_at,migration_status,applied_by_role) VALUES(?,?,?,?,?,?,'APPLIED','SYNTHETIC_TEST')",(
                mid,version,parent,digest,fp,timestamp))
            db.commit()
        except Exception:
            if db.in_transaction:db.rollback()
            raise
        applied.append(version)
    errors=db.execute('PRAGMA foreign_key_check').fetchall()
    if errors:raise ValueError('FK integrity errors after migration: '+repr(errors))
    tables=[r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    if len(tables)!=30:raise AssertionError('Expected 30 A0 tables; got '+str(len(tables)))
    last=db.execute('SELECT target_schema_sha256 FROM gov_schema_migrations WHERE migration_version=?',(files[-1].stem,)).fetchone()
    if not last or last['target_schema_sha256']!=schema_fingerprint(db):
        raise ValueError('Schema fingerprint drift detected')
    return applied
