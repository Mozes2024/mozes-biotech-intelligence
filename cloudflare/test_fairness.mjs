import assert from 'node:assert/strict';
import {database,state} from './test-db.mjs';
import {HotEdge} from './src/hot-edge.js';
import {acknowledge} from './src/edge-contract.js';

// Retained production identities, isolated fixture DB and mocked GitHub dispatch.
// A blocked publisher means no processing receipt/ACK, not a failed GitHub POST.
const fixtures=[
 ['GKOS','EDGE-3d3a4fb4e479a3864a65e58b','businesswire',171],
 ['VIR','EDGE-3a1f8b2daff5876b5fe398c3','businesswire',165],
 ['ALMS','EDGE-0355d6c1d3230e9770d460fa','globenewswire',40],
 ['GKOS','EDGE-ad40ba0f5fd5b6363342b9ac','businesswire',0],
 ['SGMT','EDGE-7c12c33cf21cf04ab3d56122','globenewswire',0],
 ['ATOS','EDGE-d221aba82c9b4438bffd21e4','sec',0],
];
const start=Date.parse('2026-10-10T08:00:00Z');
const stamp=minute=>new Date(start+minute*60000).toISOString();
function seed(db,rows=fixtures){
 rows.forEach(([ticker,id,source,attempts],i)=>db.sqlite.prepare(`INSERT INTO edge_events
 (event_id,source,source_url,ticker,first_seen_at,polarity,material,analysis_status,enrichment_attempts,enrichment_retry_at)
 VALUES(?,?,?,?,?,'unknown',1,'complete',?,?)`).run(id,source,`https://www.${source==='sec'?'sec.gov':source+'.com'}/fixture`,ticker,
 new Date(start-86400000+i*60000).toISOString(),attempts,attempts?stamp(-1):null));
}
const env=db=>({ALERTS_DB:db,GITHUB_TOKEN:'offline-fixture',GITHUB_REPO:'fixture/repo'});
const originalFetch=fetch;
let dispatched=[];
globalThis.fetch=async(url,options)=>{
 assert.equal(new URL(url).hostname,'api.github.com','queue never fetches/bypasses a publisher');
 dispatched.push(JSON.parse(options.body).inputs.edge_event_id);
 return new Response(null,{status:204});
};
try {
 const db=database(),s=state();seed(db);let edge=new HotEdge(s,env(db));
 await edge.flushPending(stamp(0));
 assert.deepEqual(dispatched,fixtures.slice(0,3).map(r=>r[1]));
 await edge.flushPending(stamp(2));assert.equal(dispatched.length,3,'active attempts stay bounded');
 edge=new HotEdge(s,env(db)); // Cursor must survive a Durable Object restart.
 await edge.flushPending(stamp(16));
 assert.equal(new Set(dispatched).size,6,'older blocked sources must not starve the other three events');
 for(let cycle=2;cycle<8;cycle++)await edge.flushPending(stamp(cycle*16));
 const counts=fixtures.map(r=>dispatched.filter(id=>id===r[1]).length);
 assert.ok(Math.max(...counts)-Math.min(...counts)<=1,'finite backlog receives bounded round-robin service');
 assert.equal(db.sqlite.prepare('SELECT COUNT(*) AS n FROM edge_events WHERE enrichment_ack_at IS NOT NULL').get().n,0);
 assert.equal(db.sqlite.prepare('SELECT COUNT(*) AS n FROM edge_events').get().n,6,'identities/history unchanged');

 // A growing tail must not prevent older due retries from ever receiving another turn.
 dispatched=[];const growing=database();seed(growing);const rotating=new HotEdge(state(),env(growing));
 await rotating.flushPending(stamp(0));
 for(let cycle=1;cycle<7;cycle++){
  for(let i=0;i<3;i++)growing.sqlite.prepare(`INSERT INTO edge_events
  (event_id,source,source_url,ticker,first_seen_at,polarity,material,analysis_status)
  VALUES(?,'businesswire','https://www.businesswire.com/fixture','NEW',?,'unknown',1,'complete')`)
  .run('EDGE-'+(200+cycle*3+i).toString(16).padStart(24,'0'),stamp(cycle*16-1));
  await rotating.flushPending(stamp(cycle*16));
 }
 assert.ok(fixtures.every(r=>dispatched.filter(id=>id===r[1]).length>=2),'frozen rounds prevent continuous-arrival starvation');
 assert.ok(dispatched.some(id=>!fixtures.some(r=>r[1]===id)),'new arrivals also receive service');

 // A publisher Retry-After represented by a future durable deadline remains ineligible.
 dispatched=[];const future=database();seed(future);
 future.sqlite.prepare('UPDATE edge_events SET enrichment_retry_at=? WHERE event_id=?').run(stamp(180),fixtures[2][1]);
 const waiting=new HotEdge(state(),env(future));
 await waiting.flushPending(stamp(0));await waiting.flushPending(stamp(16));
 assert.ok(!dispatched.includes(fixtures[2][1]));
 assert.equal(future.sqlite.prepare('SELECT enrichment_retry_at FROM edge_events WHERE event_id=?').get(fixtures[2][1]).enrichment_retry_at,stamp(180));

 // Even overlapping stale capacity snapshots cannot claim more than three rows.
 dispatched=[];const concurrent=database();seed(concurrent);
 const otherState=state();
 otherState.memory.set('delivery_cursor',{first_seen_at:new Date(start-86400000+2*60000).toISOString(),event_id:fixtures[2][1],round_cutoff:stamp(0)});
 const one=new HotEdge(state(),env(concurrent)),two=new HotEdge(otherState,env(concurrent));
 await Promise.all([one.flushPending(stamp(0)),one.flushPending(stamp(0)),two.flushPending(stamp(0))]);
 assert.equal(dispatched.length,3);assert.equal(new Set(dispatched).size,3);
 assert.equal(concurrent.sqlite.prepare('SELECT COUNT(*) AS n FROM edge_events WHERE enrichment_retry_at>?').get(stamp(0)).n,3);

 // Local fixture ACK represents completed processing AFTER a successful durable upload.
 const id=dispatched[0],body={edge_event_id:id,status:'completed',change_id:'CHG-'+'a'.repeat(24),github_run_id:'42',completed_at:stamp(1),alert_ids:[],delivery:[]};
 const ack=()=>new Request('https://edge.example/edge/ack',{method:'POST',headers:{Authorization:'Bearer fixture'},body:JSON.stringify(body)});
 const ackEnv={ALERTS_DB:concurrent,EDGE_SYNC_TOKEN:'fixture'};
 assert.equal((await acknowledge(ack(),ackEnv,stamp(1))).status,200);
 const saved=concurrent.sqlite.prepare('SELECT enrichment_ack_at FROM edge_events WHERE event_id=?').get(id).enrichment_ack_at;
 assert.equal((await acknowledge(ack(),ackEnv,stamp(2))).status,200);
 assert.equal(concurrent.sqlite.prepare('SELECT enrichment_ack_at FROM edge_events WHERE event_id=?').get(id).enrichment_ack_at,saved);
 await one.flushPending(stamp(16));
 assert.equal(dispatched.filter(row=>row===id).length,1,'completed events never dispatch again');

 // A failed dispatch cannot pin the cursor or silently complete an event.
 dispatched=[];const rejected=database(),rejectedState=state();seed(rejected);
 globalThis.fetch=async(_url,options)=>{dispatched.push(JSON.parse(options.body).inputs.edge_event_id);return new Response(null,{status:503});};
 const retrying=new HotEdge(rejectedState,env(rejected));
 await assert.rejects(()=>retrying.flushPending(stamp(0)),/pending downstream/);
 await retrying.flushPending(stamp(16));assert.equal(dispatched.length,3,'dispatch backoff remains authoritative');
 await assert.rejects(()=>retrying.flushPending(stamp(64)),/pending downstream/);
 assert.equal(new Set(dispatched).size,6);
 assert.equal(rejected.sqlite.prepare('SELECT COUNT(*) AS n FROM edge_events WHERE enrichment_ack_at IS NOT NULL').get().n,0);

 // When a queue empties, clear the hint rather than repeatedly scanning its head.
 rejected.sqlite.exec("UPDATE edge_events SET enrichment_ack_at='fixture-completed'");
 await retrying.flushPending(stamp(180));assert.equal(rejectedState.memory.get('delivery_cursor'),null);
 const prior=rejected.counts.queries;await retrying.flushPending(stamp(182));
 assert.equal(rejected.counts.queries-prior,2,'empty queue uses count + one lookup');

 // New work behind the cursor and equal timestamps is reached on wrap, without duplicate selection.
 dispatched=[];const tied=database(),tiedState=state();seed(tied);
 tied.sqlite.exec("UPDATE edge_events SET first_seen_at='2026-10-09T00:00:00Z'");
 globalThis.fetch=async(_url,options)=>{dispatched.push(JSON.parse(options.body).inputs.edge_event_id);return new Response(null,{status:204});};
 const ordered=new HotEdge(tiedState,env(tied));await ordered.flushPending(stamp(0));await ordered.flushPending(stamp(16));
 assert.equal(new Set(dispatched).size,6,'event ID breaks timestamp ties');
 tiedState.memory.set('delivery_cursor',{first_seen_at:'invalid',event_id:'invalid'});
 await new HotEdge(tiedState,env(tied)).flushPending(stamp(32));
 assert.equal(dispatched.length,9,'corrupt scheduling hint falls back safely to durable queue');

 dispatched=[];const changed=database();seed(changed);
 const downgraded=new HotEdge(state(),env(changed));
 const stale=changed.sqlite.prepare('SELECT * FROM edge_events WHERE event_id=?').get(fixtures[0][1]);
 changed.sqlite.prepare('UPDATE edge_events SET material=0 WHERE event_id=?').run(stale.event_id);
 await downgraded.deliver(stale,stamp(0));assert.equal(dispatched.length,0,'atomic claim rechecks current eligibility');

 // A cursor write failure stops before external dispatch and cannot lose durable work.
 const failedState=state(),put=failedState.storage.put;failedState.storage.put=async()=>{throw Error('fixture storage failure');};
 const failedHint=new HotEdge(failedState,env(changed));
 await assert.rejects(()=>failedHint.flushPending(stamp(0)),/storage failure/);assert.equal(dispatched.length,0);
 failedState.storage.put=put;await failedHint.flushPending(stamp(0));assert.equal(dispatched.length,3);
 console.log('edge fairness: blocked-source rotation, restart, deadlines, concurrency and ACK deduplication passed');
} finally {globalThis.fetch=originalFetch;}
