import assert from 'node:assert/strict';
import {database,state} from './test-db.mjs';
import {HotEdge} from './src/hot-edge.js';
import {activation,queueHealth} from './src/forward-policy.js';
import {eventTrace} from './src/edge-contract.js';
import {candidates} from './src/candidate-contract.js';

const NOW='2026-10-20T18:10:00.000Z',FROM='2026-10-20T18:00:00.000Z';
const RealDate=globalThis.Date,originalFetch=globalThis.fetch;
globalThis.Date=class extends RealDate{constructor(...v){super(...(v.length?v:[NOW]));}static now(){return RealDate.parse(NOW);}};
try{
 const db=database(),storage=state(),env={ALERTS_DB:db,EDGE_FORWARD_ONLY_FROM:FROM,EDGE_SYNC_TOKEN:'offline',GITHUB_TOKEN:'offline',GITHUB_REPO:'offline/repo',SEC_USER_AGENT:'offline test'};
 assert.equal(db.sqlite.prepare("SELECT count(*) n FROM pragma_table_info('edge_candidates') WHERE name='source_item_id'").get().n,0);
 const id=i=>'EDGE-'+i.toString(16).padStart(24,'0');
 const insert=(i,at,attempts=0,published=at)=>db.sqlite.prepare(`INSERT INTO edge_events
  (event_id,source,source_url,ticker,first_seen_at,published_at,polarity,material,analysis_status,enrichment_attempts)
  VALUES(?,'globenewswire','https://www.globenewswire.com/new','TEST',?,?,'positive',1,'complete',?)`).run(id(i),at,published,attempts);
 for(let i=0;i<32;i++)insert(i,'2026-10-08T10:00:00.000Z',200);
 insert(50,'2026-10-20T18:01:00.000Z',2);insert(51,'2026-10-20T18:02:00.000Z',2);
 insert(52,'2026-10-20T18:09:00.000Z');insert(53,'2026-10-20T18:08:00.000Z',0,null);
 const rapp='EDGE-aadb760b353397b1f5039db7';
 db.sqlite.prepare(`INSERT INTO edge_events(event_id,source,source_url,ticker,first_seen_at,accepted_at,polarity,material,analysis_status,enrichment_ack_at,enrichment_attempts)
 VALUES(?,'sec','https://www.sec.gov/historical','RAPP','2026-10-20T10:07:30.225Z','2026-10-20T06:05:17-04:00','positive',1,'complete','2026-10-20T14:30:58.734Z',1)`).run(rapp);
 const history=db.sqlite.prepare('SELECT * FROM edge_events WHERE event_id=?').get(rapp);
 const old=db.sqlite.prepare('SELECT * FROM edge_events WHERE event_id=?').get(id(0));
 const dispatched=[];
 globalThis.fetch=async(url,options)=>{assert.equal(new URL(url).hostname,'api.github.com');dispatched.push(JSON.parse(options.body).inputs.edge_event_id);return new Response(null,{status:204});};
 const edge=new HotEdge(storage,env);
 await Promise.all([edge.flushPending(NOW),edge.flushPending(NOW)]);
 assert.equal(dispatched.length,3);assert.ok(dispatched.includes(id(52)),'fresh first attempt admitted beside two fair retries');
 assert.equal(new Set(dispatched).size,3);
 await new HotEdge(storage,env).flushPending(NOW);
 assert.equal(dispatched.length,3,'global cap survives restart/overlap');
 assert.deepEqual(db.sqlite.prepare('SELECT * FROM edge_events WHERE event_id=?').get(rapp),history);
 assert.deepEqual(db.sqlite.prepare('SELECT * FROM edge_events WHERE event_id=?').get(id(0)),old,'historical retries retained unchanged');
 const health=await queueHealth(db,env,NOW);
 assert.equal(health.enrichment_manual_review,33);assert.equal(health.awaiting_completion,3);
 assert.equal(health.enrichment_pending,36);
 insert(54,'2026-10-20T18:05:00.000Z',5);
 await edge.deliver(db.sqlite.prepare('SELECT * FROM edge_events WHERE event_id=?').get(id(54)),NOW);
 assert.equal(dispatched.length,3,'exhausted retries do not consume further dispatches');
 assert.equal((await queueHealth(db,env,NOW)).enrichment_manual_review,34);
 // A fresh admission into the sole free slot preserves the old retry tail.
 insert(55,'2026-10-20T18:09:30.000Z');insert(56,'2026-10-20T18:05:00.000Z',2);
 db.sqlite.prepare('UPDATE edge_events SET enrichment_retry_at=NULL WHERE event_id=?').run(id(50));
 await edge.flushPending(NOW);assert.equal(dispatched.at(-1),id(55));
 db.sqlite.prepare('UPDATE edge_events SET enrichment_retry_at=NULL WHERE event_id=?').run(id(51));
 await edge.flushPending(NOW);assert.equal(dispatched.at(-1),id(56),'resume the tail rather than restart at the oldest retry');
 const request=x=>new Request('https://edge.example/edge/event?id='+x,{headers:{Authorization:'Bearer offline'}});
 assert.equal((await eventTrace(request(id(0)),env)).status,409,'queued historical work cannot become a fresh alert');
 assert.equal((await eventTrace(request(rapp),env)).status,200,'authentic acknowledged history remains readable');
 assert.equal(activation(env,NOW),FROM);
 assert.throws(()=>activation({},NOW),/activation/);
 assert.throws(()=>activation({...env,EDGE_FORWARD_ONLY_FROM:'2026-10-21T00:00:00Z'},NOW),/activation/);
 // Legacy candidate route retains old rows, but offers only genuinely forward work.
 for(const [n,at] of [[60,'2026-10-08T00:00:00Z'],[61,'2026-10-20T18:09:00Z']])db.sqlite.prepare(`INSERT INTO edge_candidates
  (candidate_id,source,source_url,headline,summary,published_at,first_seen_at,classification_json,lifecycle,suppression_reason,history_json,content_hash)
  VALUES(?,'globenewswire','https://www.globenewswire.com/new','material trial results','',?,?,'{}','CLASSIFIED','issuer_unresolved','[]','hash')`).run(id(n),at,at);
 const candidateResponse=await candidates(new Request('https://edge.example/edge/candidates',{headers:{Authorization:'Bearer offline'}}),env);
 assert.deepEqual((await candidateResponse.json()).candidates.map(r=>r.candidate_id),[id(61)]);
 assert.equal(db.sqlite.prepare('SELECT count(*) n FROM edge_candidates').get().n,2);
 // Exact deterministic MESO size limit: no fake completion or ACK, no repeated fetch.
 const meso='EDGE-fe3b6ebc9a7186e22f7d78b8',base='https://www.sec.gov/Archives/edgar/data/1345099/000134509926000087/';
 db.sqlite.prepare(`INSERT INTO edge_events(event_id,source,source_url,filing_index_url,ticker,cik,accession,first_seen_at,polarity,material,analysis_status,analysis_attempts)
 VALUES(?,'sec',?,?,'MESO','1345099','0001345099-26-000087','2026-10-13T10:29:04Z','unknown',0,'failed',173)`).run(meso,base+'index.htm',base+'index.htm');
 db.sqlite.prepare("UPDATE edge_events SET analysis_retry_at='2026-10-21T00:00:00Z',last_analysis_error='primary document analysis failed' WHERE event_id=?").run(meso);
 let fetched=0;globalThis.fetch=async url=>{fetched++;return new Response(String(url).endsWith('index.htm')?'<tr><td>EX-99.1</td><td><a href="exhibit991annualreportag.htm">Exhibit 99.1</a></td></tr>':'x'.repeat(1092465));};
 await edge.analyzePending(NOW);
 const blocked=db.sqlite.prepare('SELECT * FROM edge_events WHERE event_id=?').get(meso);
 assert.equal(blocked.analysis_status,'blocked');assert.equal(blocked.analysis_attempts,174);assert.equal(blocked.analysis_retry_at,null);
 assert.equal(blocked.analysis_completed_at,null);assert.equal(blocked.enrichment_ack_at,null);assert.match(blocked.last_analysis_error,/size limit/);
 await edge.analyzePending('2026-10-21T18:10:00Z');assert.equal(fetched,2);
 assert.equal((await queueHealth(db,env,NOW)).analysis_blocked,1);
 assert.equal(db.sqlite.prepare('SELECT count(*) n FROM edge_events').get().n,41);
 const transient=database(),transientState=state();
 transient.sqlite.prepare(`INSERT INTO edge_events(event_id,source,source_url,filing_index_url,ticker,cik,accession,first_seen_at,polarity,material,analysis_status,analysis_attempts,analysis_retry_at,last_analysis_error)
 VALUES(?,'sec',?,?,'MESO','1345099','0001345099-26-000087','2026-10-13T10:29:04Z','unknown',0,'failed',173,'2026-10-21T00:00:00Z','primary document analysis failed')`).run(meso,base+'index.htm',base+'index.htm');
 let failedFetches=0;globalThis.fetch=async()=>{failedFetches++;return new Response('unavailable',{status:503});};
 const retryEdge=new HotEdge(transientState,{...env,ALERTS_DB:transient});
 await retryEdge.analyzePending(NOW);await new HotEdge(transientState,{...env,ALERTS_DB:transient}).analyzePending(NOW);
 assert.equal(failedFetches,1,'one-time review does not defeat transient backoff after restart');
 const failed=transient.sqlite.prepare('SELECT * FROM edge_events WHERE event_id=?').get(meso);
 assert.equal(failed.analysis_status,'failed');assert.equal(failed.last_analysis_error,'HTTP 503');assert.ok(failed.analysis_retry_at);
 console.log('Stage A only: forward-only, fair fresh dispatch, cap, restart, retained history, candidates, retry budget and blocked MESO passed');
}finally{globalThis.fetch=originalFetch;globalThis.Date=RealDate;}
