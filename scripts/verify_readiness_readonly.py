"""One read-only assessment. Never call eligible(), claim(), process() or a writer."""
import contextlib
import io
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import incident_recovery as m

LABELS = ["worker_settings", "worker_deployment", "worker_schedule", "worker_content", "d1_metadata", "d1_select", "d1_analytics"]
state = {"phase":"configuration", "http":None, "network":False, "calls":0}

def emit(phase, status, category="validated", http=None):
    print(phase + " " + status + " " + category + " http=" + (str(http) if http else "none"))

def main():
    original_open = urllib.request.OpenerDirector.open
    def observed(self, *args, **kwargs):
        try: return original_open(self, *args, **kwargs)
        except urllib.error.HTTPError as e:
            state['http']=e.code
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            state['network']=True
            raise
    urllib.request.OpenerDirector.open=observed
    original_cf=m.cf
    def cf(*args, **kwargs):
        if state['calls']>=7: raise RuntimeError('unexpected read contract')
        state['phase']=LABELS[state['calls']]
        value=original_cf(*args, **kwargs) # Exact reviewed allowlist remains authoritative.
        state['calls']+=1
        return value
    m.cf=cf
    now=datetime.now(timezone.utc)
    if not (m.START <= now < m.END):
        emit('incident_deadline','FAIL','expired_or_not_started')
        return
    emit('incident_deadline','PASS')
    state['phase']='provider'
    proof=m.provider_gate(now)
    for label in LABELS: emit(label,'PASS')
    emit('worker_identity_settings_cadence_schema','PASS')
    emit('immutable_edge_fingerprints_eight_ACKs','PASS')
    emit('write_freshness_current_hour_headroom','PASS' if proof['capacity_demonstrated'] else 'FAIL','capacity_demonstrated='+str(proof['capacity_demonstrated']).lower())
    state.update(phase='pending_edge_checks',http=None,network=False)
    m.pending_gate()
    emit('pending_edge_checks','PASS')
    state['phase']='public_feed'
    feed=m.public_feed()
    if feed.get('revision')!='246b6cd8797afa35a7986eba94f102d1f0975b552bd4af6dfc9b9f529587b73c' or len(feed['alerts'])!=40:
        raise RuntimeError('feed integrity mismatch')
    emit('public_feed','PASS')
    state['phase']='checkpoint_producer_history'
    with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
        m.restore.restore(m.SOURCE,recovery_sha256=m.SHA256,verify_only=True,now=now)
    emit('checkpoint_producer_history','PASS')
    state['phase']='claim'
    if m.ref() is not None: raise RuntimeError('claim exists')
    emit('claim_absent','PASS')
    emit('assessment','PASS' if proof['capacity_demonstrated'] else 'FAIL','premerge_readonly_evidence_only')

if __name__=='__main__':
    try: main()
    except Exception:
        code=state['http']
        reason=('redirect' if 300<=code<400 else 'http_'+str(code)) if code else ('network_or_timeout' if state['network'] else 'contract_or_shape_validation')
        emit(state['phase'],'FAIL',reason,code)
