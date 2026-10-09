import {authorized,readJson,validEdgeId} from './edge-contract.js';

export async function candidates(request,env){
 if(request.method!=='GET'||!authorized(request,env)) return new Response('Unauthorized',{status:401});
 const rows=(await env.ALERTS_DB.prepare("SELECT * FROM edge_candidates WHERE suppression_reason IN ('issuer_unresolved','source_unavailable','classification_uncertain') AND completed_at IS NULL AND (verification_retry_at IS NULL OR verification_retry_at<=?) ORDER BY first_seen_at LIMIT 20").bind(new Date().toISOString()).all()).results||[];
 return Response.json({candidates:rows});
}

export async function candidateAck(request,env){
 if(request.method!=='POST'||!authorized(request,env)) return new Response('Unauthorized',{status:401});
 let body;try{body=await readJson(request,16384);}catch(response){return response;}
 if(!validEdgeId(body?.candidate_id)||!/^\d{1,20}$/.test(body.github_run_id||'')||!['PUBLISHED','SUPPRESSED'].includes(body.lifecycle)||
    !['issuer_unresolved','source_unavailable','duplicate','low_materiality','routine_conference','classification_uncertain','outside_scope',null].includes(body.suppression_reason??null)||
    body.change_id&&!/^CHG-[a-f0-9]{24}$/.test(body.change_id)||body.catalyst_id&&!/^CAT-[a-f0-9]{24}$/.test(body.catalyst_id)||
    body.ticker&&!/^[A-Z][A-Z0-9]{0,9}$/.test(body.ticker)||body.cik&&!/^\d{1,10}$/.test(body.cik)) return new Response('Invalid receipt',{status:400});
 const row=await env.ALERTS_DB.prepare('SELECT * FROM edge_candidates WHERE candidate_id=?').bind(body.candidate_id).first();
 if(!row)return new Response('Unknown candidate',{status:404});
 if(body.content_hash && body.content_hash!==row.content_hash)return new Response('Evidence changed; reconcile again',{status:409});
 if(body.lifecycle==='PUBLISHED'&&(!body.change_id||!body.ticker||!body.cik))return new Response('Missing verified identity',{status:400});
 if(row.completed_at||row.github_run_id===body.github_run_id)return Response.json({ok:true,duplicate:true});
 const now=new Date().toISOString(),history=JSON.parse(row.history_json);
 if(body.lifecycle==='PUBLISHED')history.push(...['RESOLVED','CLASSIFIED','ENRICHED'].map(state=>({state,at:now})));
 history.push({state:body.lifecycle,at:now,reason:body.suppression_reason||null});
 const retry=['issuer_unresolved','source_unavailable'].includes(body.suppression_reason);
 const retryAt=retry?new Date(Date.now()+Math.min(86400000,300000*2**Math.min(8,row.verification_attempts||0))).toISOString():null;
 await env.ALERTS_DB.prepare('UPDATE edge_candidates SET lifecycle=?,suppression_reason=?,ticker=COALESCE(?,ticker),cik=COALESCE(?,cik),github_run_id=?,change_id=?,catalyst_id=?,completed_at=?,history_json=?,verification_attempts=verification_attempts+1,verification_retry_at=? WHERE candidate_id=? AND completed_at IS NULL')
  .bind(body.lifecycle,body.suppression_reason||null,body.ticker||null,body.cik||null,body.github_run_id,body.change_id||null,body.catalyst_id||null,
    retry?null:now,JSON.stringify(history.slice(-10)),retryAt,body.candidate_id).run();
 return Response.json({ok:true});
}

export async function investigate(request,env){
 if(request.method!=='GET'||!authorized(request,env))return new Response('Unauthorized',{status:401});
 const query=new URL(request.url).searchParams.get('q')||'';
 if(!query||query.length>200)return new Response('Invalid query',{status:400});
 const pattern='%'+query.replace(/[\\%_]/g,c=>'\\'+c)+'%';
 const rows=(await env.ALERTS_DB.prepare("SELECT * FROM edge_candidates WHERE ticker=? OR ticker_hint=? OR headline LIKE ? ESCAPE '\\' OR source_url LIKE ? ESCAPE '\\' ORDER BY first_seen_at DESC LIMIT 30")
   .bind(query.toUpperCase(),query.toUpperCase(),pattern,pattern).all()).results||[];
 return Response.json({events:rows});
}

export async function candidateStats(db){
 const totals=await db.prepare("SELECT COUNT(*) AS events_discovered,SUM(CASE WHEN ticker IS NOT NULL THEN 1 ELSE 0 END) AS issuers_resolved,SUM(CASE WHEN ticker IS NULL THEN 1 ELSE 0 END) AS rejected_before_resolution,SUM(CASE WHEN classification_json IS NOT NULL THEN 1 ELSE 0 END) AS events_classified,SUM(CASE WHEN lifecycle='PUBLISHED' THEN 1 ELSE 0 END) AS events_published,SUM(CASE WHEN catalyst_id IS NOT NULL THEN 1 ELSE 0 END) AS catalysts_created_or_updated FROM edge_candidates").first();
 const reasons=(await db.prepare("SELECT suppression_reason,COUNT(*) AS n FROM edge_candidates WHERE suppression_reason IS NOT NULL GROUP BY suppression_reason").all()).results||[];
 return {...totals,suppressed_by_reason:Object.fromEntries(reasons.map(r=>[r.suppression_reason,r.n])),retention_days:null,max_candidates:null};
}
