"""Read-only counterfactual alert assessment; never enqueue or rewrite history."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import re
from datetime import datetime

from mozes import alert_dispatch
from mozes.materiality import classify_outcome

BASELINE = '6f939b8299a5d96dc4e175e5e75baf1ec5636080'


def assess(path):
    before_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    baseline = {}
    source = subprocess.check_output(['git','show',BASELINE+':mozes/materiality.py'], text=True, encoding='utf-8')
    exec(compile(source,'baseline-materiality','exec'),baseline)
    old_dispatch = dict(alert_dispatch.__dict__)
    source = subprocess.check_output(['git','show',BASELINE+':mozes/alert_dispatch.py'],text=True,encoding='utf-8')
    source = '\n'.join(line for line in source.splitlines() if not line.startswith('from .materiality import '))
    old_dispatch.update({key:baseline[key] for key in ('ALERTABLE_CHANGE_TYPES','PRIORITY_RANK','alert_priority','classify_outcome','should_enqueue')})
    exec(compile(source,'baseline-dispatch','exec'),old_dispatch)
    counts = dict(polarity_changes=0,priority_changes=0,milestones_previously_positive=0,
                  historical_previously_outcomes=0,suppressed_old_popup_candidates=0,confirmed_eligible=0)
    before, after, changes, examples = Counter(), Counter(), [], []
    published_ids = {r[0] for r in conn.execute("SELECT DISTINCT change_id FROM alert_outbox WHERE stage='stage1'")}
    published = dict(population=len(published_ids),suppressed_interrupts=0,confirmed_eligible=0)
    for row in conn.execute('SELECT * FROM change_events ORDER BY detected_at,change_id'):
        old = old_dispatch['build_stage1_payload'](conn,row['change_id'])
        new = alert_dispatch.build_stage1_payload(conn,row['change_id'])
        a,b=old['outcome'],new['outcome']
        before[(row['change_type'],a.get('polarity','unknown'),old['priority'])]+=1
        after[(row['change_type'],b.get('polarity','unknown'),new['priority'])]+=1
        counts['polarity_changes']+=a.get('polarity')!=b.get('polarity')
        counts['priority_changes']+=old['priority']!=new['priority']
        value=json.loads(row['new_value'] or 'null')
        source_text='\n'.join(str(value.get(k) or '') for k in ('headline','title','summary')) if isinstance(value,dict) else str(value or '')
        historical_acceptance = b.get('uncertainty_status')=='historical' and bool(re.search(r'\bFDA\s+accepted\s+for\s+(?:priority\s+)?review\b',source_text,re.I))
        counts['milestones_previously_positive']+=(b.get('event_family')=='regulatory_milestone' or historical_acceptance) and a.get('polarity')=='positive'
        counts['historical_previously_outcomes']+=b.get('uncertainty_status')=='historical' and a.get('polarity') in {'positive','negative','mixed'}
        # Legacy browser interrupted every newly observed published row; only old enqueue candidates count here.
        legacy_visible=old['priority'] in {'P1','P2'} and row['change_type'] in baseline['ALERTABLE_CHANGE_TYPES']
        counts['suppressed_old_popup_candidates']+=legacy_visible and not new['popup_eligible']
        counts['confirmed_eligible']+=new['popup_eligible']
        if row['change_id'] in published_ids:
            published['suppressed_interrupts']+=not new['popup_eligible']
            published['confirmed_eligible']+=new['popup_eligible']
        item=dict(change_id=row['change_id'],ticker=row['ticker'],source_url=row['source_url'],source_hash=row['source_hash'],
                  detected_at=row['detected_at'],before=dict(polarity=a.get('polarity'),priority=old['priority']),
                  after={**b,'priority':new['priority'],'popup_eligible':new['popup_eligible']},new_value=json.loads(row['new_value'] or 'null'))
        if a.get('polarity')!=b.get('polarity') or old['priority']!=new['priority']:changes.append(item)
        if new['popup_eligible'] or b.get('event_family') in {'regulatory_milestone','corporate_action'}:examples.append(item)
    documents=[]
    for row in conn.execute('SELECT * FROM alert_source_documents ORDER BY fetched_at'):
        text=row['normalized_text']
        assert datetime.fromisoformat(row['fetched_at']) <= datetime.fromisoformat('2026-10-12T17:26:54+00:00')
        assert hashlib.sha256(text.encode()).hexdigest() == row['content_hash']
        documents.append(dict(document_id=row['document_id'],source_url=row['url'],source_hash=row['content_hash'],
                              retrieved_at=row['fetched_at'],before=baseline['classify_outcome'](text),after=classify_outcome(text,basis='source_text',as_of=row['fetched_at']),text=text[:1600]))
    tables=['change_events','source_archive','alert_outbox','alert_analyses','alert_source_documents','forward_candidates']
    preserved={name:conn.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in tables}
    conn.close()
    assert before_hash==hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(schema=1,baseline_sha=BASELINE,checkpoint_sha256=before_hash,query_only=True,integrity='ok',foreign_key_violations=0,
                population=sum(before.values()),counts=counts,published_counterfactual=published,before=[dict(change_type=k[0],polarity=k[1],priority=k[2],count=v) for k,v in sorted(before.items())],
                after=[dict(change_type=k[0],polarity=k[1],priority=k[2],count=v) for k,v in sorted(after.items())],
                changes=changes,examples=examples,source_documents=documents,preserved_counts=preserved,
                limitation='Counterfactual future alert policy, not a rewrite of stored alerts. Archive-text classifications are separate retrospective evidence checks and do not change historical priorities or PIT cutoffs.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('checkpoint',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();report=assess(args.checkpoint);args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('checkpoint_sha256','population','counts','preserved_counts')},indent=2))
