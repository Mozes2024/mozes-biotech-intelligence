"""Execute exact built-candidate SQL against SQLite, including transaction failures."""
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def candidate(tmp_path):
    dest = tmp_path / 'candidate'
    subprocess.run([sys.executable, str(ROOT/'scripts/build_stage_a_write_guard.py'), str(dest)], check=True, capture_output=True)
    return dest

@pytest.fixture
def db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE edge_issuers(cik TEXT PRIMARY KEY,ticker TEXT,company TEXT,confidence REAL,source TEXT,active INTEGER,updated_at TEXT)')
    return conn

def sql(candidate):
    text = (candidate/'cloudflare/src/index.js').read_text()
    deactivate = re.search(r'const statements = \[db.prepare\("([^"]+)"\)', text).group(1)
    upsert = re.search(r'const upsert = db.prepare\("([^"]+)"\)', text).group(1)
    return deactivate, upsert

def apply(db, candidate, rows, now):
    deactivate, upsert = sql(candidate)
    before = db.total_changes
    with db:
        db.execute(deactivate, (now, json.dumps([r[0] for r in rows])))
        for row in rows:
            db.execute(upsert, (*row, now))
    return db.total_changes-before

A = ('123','AAA','Alpha Bio',.99,'SEC-v2C-equity')
B = ('456','BBB','Beta Bio',.98,'SEC-v2C-equity')

def test_identical_snapshot_zero_writes_preserves_timestamp(db,candidate):
    assert apply(db,candidate,[A,B],'t1')==2
    assert apply(db,candidate,[A,B],'t2')==0
    assert {r['updated_at'] for r in db.execute('SELECT * FROM edge_issuers')}=={'t1'}

@pytest.mark.parametrize('index,value',[(1,'NEW'),(2,'Changed Bio'),(3,.97),(4,'new-source')])
def test_changed_fields_update_only_changed_row(db,candidate,index,value):
    apply(db,candidate,[A,B],'t1')
    changed=list(A);changed[index]=value
    assert apply(db,candidate,[changed,B],'t2')==1
    row=db.execute('SELECT * FROM edge_issuers WHERE cik=?',('123',)).fetchone()
    assert tuple(row)[1:5]==tuple(changed)[1:5]
    assert row['active']==1 and row['updated_at']=='t2'
    assert db.execute("SELECT updated_at FROM edge_issuers WHERE cik='456'").fetchone()[0]=='t1'

def test_remove_reactivate_new_and_inactive_noop(db,candidate):
    apply(db,candidate,[A,B],'t1')
    assert apply(db,candidate,[A],'t2')==1
    assert tuple(db.execute("SELECT active,updated_at FROM edge_issuers WHERE cik='456'").fetchone())==(0,'t2')
    assert apply(db,candidate,[A],'t3')==0
    assert apply(db,candidate,[A,B],'t4')==1
    assert tuple(db.execute("SELECT active,updated_at FROM edge_issuers WHERE cik='456'").fetchone())==(1,'t4')
    assert apply(db,candidate,[A,B,('789','CCC','Gamma Bio',.9,'new')],'t5')==1

def test_null_legacy_fields_repaired(db,candidate):
    db.execute('INSERT INTO edge_issuers VALUES(?,?,?,?,?,?,?)',('123',None,None,None,None,None,'t0'));db.commit()
    assert apply(db,candidate,[A],'t1')==1
    assert tuple(db.execute('SELECT * FROM edge_issuers').fetchone())==(*A,1,'t1')

def test_failure_rolls_back_removal_and_prior_upserts(db,candidate):
    apply(db,candidate,[A,B],'t1')
    before=[tuple(r) for r in db.execute('SELECT * FROM edge_issuers ORDER BY cik')]
    db.execute("CREATE TRIGGER reject_bad BEFORE INSERT ON edge_issuers WHEN NEW.ticker='BAD' BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(sqlite3.IntegrityError):
        apply(db,candidate,[('123','NEW','Changed Bio',.9,'new'),('789','BAD','Fail Bio',.9,'new')],'t2')
    assert [tuple(r) for r in db.execute('SELECT * FROM edge_issuers ORDER BY cik')]==before

def test_bundle_only_expected_index_sql_diff(candidate,tmp_path):
    baseline=tmp_path/'base'
    subprocess.run([sys.executable,str(ROOT/'scripts/build_stage_a_health_cache.py'),str(baseline)],check=True,capture_output=True)
    files={p.relative_to(baseline) for p in baseline.rglob('*') if p.is_file()}
    assert files=={p.relative_to(candidate) for p in candidate.rglob('*') if p.is_file()}
    changed=[p for p in files if (baseline/p).read_bytes()!=(candidate/p).read_bytes()]
    assert changed==[Path('cloudflare/src/index.js')]
    import importlib.util
    spec=importlib.util.spec_from_file_location('guard',ROOT/'scripts/build_stage_a_write_guard.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    original=(baseline/changed[0]).read_text()
    expected=original.replace(mod.OLD_DEACTIVATE,mod.NEW_DEACTIVATE).replace(mod.OLD_UPSERT,mod.NEW_UPSERT)
    assert expected==(candidate/changed[0]).read_text()
