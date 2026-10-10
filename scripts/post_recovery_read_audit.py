import hashlib,json,time
from mozes.monitor_recovery import validate_verification_configuration,verification_edge_request
validate_verification_configuration()
ids=['EDGE-3d3a4fb4e479a3864a65e58b','EDGE-ad40ba0f5fd5b6363342b9ac','EDGE-3a1f8b2daff5876b5fe398c3','EDGE-0355d6c1d3230e9770d460fa','EDGE-7c12c33cf21cf04ab3d56122','EDGE-d221aba82c9b4438bffd21e4','EDGE-24534abeca26dcdd08c16db6']
accessions=['0001628280-26-065404','0001628280-26-065497','0001104659-26-114571','0001628280-26-065559','0001193125-26-418560','0001213900-26-108467','0001171843-26-006551','0001193125-26-419174','0001213900-26-107727','0001493152-26-046340']
ids+=['EDGE-'+hashlib.sha256(('sec:'+a).encode()).hexdigest()[:24] for a in accessions]
keys=['event_id','ticker','source','accession','analysis_status','material','lifecycle','suppression_reason','enrichment_status','enrichment_ack_at','enrichment_change_id','enrichment_github_run_id','enrichment_alert_ids_json','enrichment_delivery_json','enrichment_attempts','enrichment_last_error','enrichment_next_attempt_at']
for event_id in dict.fromkeys(ids):
    try:
        event=verification_edge_request('event?id='+event_id)
        print(json.dumps({'requested_id':event_id,**{k:event.get(k) for k in keys}},sort_keys=True))
    except Exception as exc:
        print(json.dumps({'requested_id':event_id,'read_error_configuration':str(exc) if type(exc).__name__=='RecoveryConfigurationError' else type(exc).__name__}))
    time.sleep(1)
print('READ_ONLY_EDGE_AUDIT_FINISHED')
