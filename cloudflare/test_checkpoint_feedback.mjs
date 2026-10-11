import assert from 'node:assert/strict';
import {checkpointFeedback, RECEIPT_KEY} from './src/checkpoint-feedback.js';
const now = Date.parse('2026-10-11T20:00:00Z');
const env = {EDGE_CHECKPOINT_FEEDBACK_ENABLED:'1', EDGE_SYNC_TOKEN:'offline-only'};
const receipt = {schema:1,repository:'Mozes2024/mozes-biotech-intelligence',
  workflow:'lightweight-monitor.yml',run_id:'123',run_attempt:1,head_sha:'a'.repeat(40),
  artifact_id:456,artifact_created_at:'2026-10-11T19:59:30Z',checkpoint_at:'2026-10-11T19:59:00Z',
  db_sha256:'b'.repeat(64),proof:'actions_download_sha256_sqlite_v1'};
let writes = 0; const memory = new Map();
const storage = {get:async k=>memory.get(k),put:async(k,v)=>{writes++;memory.set(k,v);}};
const request = (body=receipt,token='offline-only',method='POST') => new Request('https://edge.internal/edge/checkpoint-feedback',
  {method,headers:{Authorization:`Bearer ${token}`},...(method==='POST'?{body:typeof body==='string'?body:JSON.stringify(body)}:{})});
const send = (body=receipt,settings=env)=>checkpointFeedback(request(body),storage,settings,now);
assert.equal((await send(receipt,{})).status,404);
assert.equal((await checkpointFeedback(request(receipt,'wrong'),storage,env,now)).status,401);
assert.equal((await checkpointFeedback(request(receipt,'offline-only','GET'),storage,env,now)).status,401);
assert.equal((await send('x'.repeat(4097))).status,413);
assert.equal((await send('not json')).status,400);
for (const change of [
  {repository:'Other/repo'},{workflow:'deep-monitor.yml'},{run_attempt:2},{run_id:123},
  {artifact_id:0},{head_sha:'bad'},{db_sha256:'bad'},{proof:'dispatch_204'},
  {checkpoint_at:'2026-10-11T19:00:00Z'}, {checkpoint_at:'2026-10-11T20:01:00Z'},
  {artifact_created_at:'2026-10-11T20:01:00Z'}, {checkpoint_at:'2026-10-11T19:59:00'},
  {extra:'unexpected'},
]) assert.equal((await send({...receipt,...change})).status,422);
assert.equal(writes,0);
assert.equal((await send()).status,200);assert.equal(writes,1);
assert.equal((await send()).status,200);assert.equal(writes,1); // durable idempotency
assert.equal((await send({...receipt,db_sha256:'c'.repeat(64)})).status,409);
assert.equal((await send({...receipt,run_id:'124',artifact_id:457,checkpoint_at:'2026-10-11T19:58:00Z'})).status,409);
const later = {...receipt,run_id:'124',artifact_id:457,checkpoint_at:'2026-10-11T19:59:40Z',artifact_created_at:'2026-10-11T19:59:50Z'};
assert.equal((await send(later)).status,200);assert.equal(writes,2);
assert.equal((await send()).status,409); // old receipt cannot rewind the durable maximum
assert.deepEqual(memory.get(RECEIPT_KEY),later);
assert.equal((await checkpointFeedback(request(later),storage,env,now+2700001)).status,422);
assert.equal(writes,2); // old replay cannot make health fresh
const {HotEdge} = await import('./src/hot-edge.js');
const concurrentMemory=new Map();
const concurrentStorage={get:async k=>concurrentMemory.get(k),put:async(k,v)=>concurrentMemory.set(k,v)};
const edge=new HotEdge({storage:concurrentStorage},env);
// Exercise the real DO queue, not an assumption that independent handlers serialize.
const realNow=Date.now;Date.now=()=>now;
try {
  const responses=await Promise.all([edge.fetch(request(later)),edge.fetch(request())]);
  assert.deepEqual(responses.map(r=>r.status),[200,409]);
  assert.deepEqual(concurrentMemory.get(RECEIPT_KEY),later);
} finally {Date.now=realNow;}
console.log('checkpoint feedback: auth, bounds, monotonicity and replay checks passed');
