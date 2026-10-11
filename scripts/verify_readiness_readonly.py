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
state = {"phase":"configuration", "http":None, "network":False, "calls":0, "queued":None}

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
        if state['phase']=='d1_select':
            stamps=[m.timestamp(e['enrichment_queued_at']) for e in value[1]['results']
                    if e['event_id'] in m.PENDING and e.get('enrichment_queued_at')]
            state['queued']=max(stamps).isoformat() if stamps else None
        state['calls']+=1
        return value
    m.cf=cf
    now=datetime.now(timezone.utc)
    if not (m.START <= now < m.END):
        emit('incident_deadline','FAIL','expired_or_not_started')
        return 1
    emit('incident_deadline','PASS')
    baseline="527133b98b68cc64a4f72afeabfed0916eff81ae"
    state['phase']='main_CI_and_disabled_controls'
    if m.api('commits/main')['sha']!=baseline: raise RuntimeError('main changed')
    runs=m.api('actions/workflows/ci.yml/runs?event=push&per_page=100')['workflow_runs']
    if not any(r['head_sha']==baseline and r['conclusion']=='success' for r in runs): raise RuntimeError('exact main CI missing')
    if os.environ.get('RECOVERY_ACTIVATION_IS_OFF')!='true' or os.environ.get('RECOVERY_MANUAL_APPROVAL_IS_UNSET')!='true': raise RuntimeError('controls changed')
    emit('main_CI_and_disabled_controls','PASS')
    state['phase']='source_artifact'
    artifact=m.api('actions/artifacts/11669812295')
    if artifact.get('expired') is not False or str(artifact['workflow_run']['id'])!=m.SOURCE or artifact.get('digest')!='sha256:8157f00682d5c415f6fca51e7d6341b714cd9d52fdf5da80b51be6e480f6c447': raise RuntimeError('source changed')
    emit('source_artifact','PASS')
    state['phase']='provider'
    proof=m.provider_gate(now)
    if state['calls'] != 7: raise RuntimeError('incomplete provider contracts')
    for label in LABELS: emit(label,'PASS')
    print('capacity_details current_hour_rows_written='+str(proof['current_hour_rows_written'])+
          ' daily_rows_written='+str(proof['daily_rows_written'])+
          ' measured_at='+now.isoformat()+
          ' qualifying_automatic_write_at='+str(proof['latest_automatic_write_at'])+
          ' latest_pending_queue_at='+str(state['queued']))
    emit('worker_identity_settings_cadence_schema','PASS')
    emit('immutable_edge_fingerprints_eight_ACKs','PASS')
    state['phase']='write_freshness_current_hour_headroom'
    emit('current_hour_write_evidence','PASS' if proof['current_hour_rows_written']>0 else 'FAIL')
    emit('automatic_write_recency','PASS' if proof['latest_automatic_write_at'] else 'FAIL')
    emit('daily_headroom','PASS')
    if not proof['capacity_demonstrated']:
        emit(state['phase'],'FAIL','capacity_demonstrated=false')
        return 1
    emit(state['phase'],'PASS','capacity_demonstrated=true')
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
    state['phase']='competing_owners'
    runs=m.api('actions/workflows/lightweight-monitor.yml/runs?per_page=100')['workflow_runs']
    if any(r['status']!='completed' for r in runs): raise RuntimeError('competing owner')
    emit('competing_owners_absent','PASS')
    final_now=datetime.now(timezone.utc)
    if (not (m.START<=final_now<m.END) or
            (final_now-m.timestamp(proof['latest_automatic_write_at'])).total_seconds()>600):
        emit('final_deadline_write_recency','FAIL','evidence_expired_during_audit')
        return 1
    emit('final_deadline_write_recency','PASS')
    emit('assessment','PASS','postmerge_readonly_evidence_only')
    return 0

def run():
    try: return main()
    except Exception as exc:
        code=exc.code if isinstance(exc,urllib.error.HTTPError) else state['http']
        reason=('redirect' if 300<=code<400 else 'http_'+str(code)) if code else ('network_or_timeout' if state['network'] else 'contract_or_shape_validation')
        emit(state['phase'],'FAIL',reason,code)
        return 1

if __name__=='__main__':
    sys.exit(run())
