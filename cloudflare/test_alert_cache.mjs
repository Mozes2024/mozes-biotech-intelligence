import test from 'node:test';
import assert from 'node:assert/strict';
import {HotEdge} from './src/hot-edge.js';
import {publicFeed} from './src/alert-feed.js';

const urgent={change_id:'CHG-456',priority:'P1',polarity:'negative',
 issuer_verified:true,confirmed_outcome:true,needs_review:false,popup_eligible:true,
 semantic_version:'alert-evidence-v1',evidence_basis:'source_text',
 event_family:'clinical_outcome',event_outcome:'primary_endpoint_failed',
 source_url:'https://company.example/source',delivery:{email:{status:'sent',attempts:1,sent_at:'2026-10-13T00:00:00Z'}},
 explanation:{basis:'source_text',method:'source-rules-v2',status:'complete',
  evidence:[{id:'source-1',quote:'The trial failed its primary endpoint.',url:'https://company.example/source',source_hash:'d'.repeat(64),start:0,end:38}]}};
function snapshot(sequence=1){return publicFeed({schema:1,sequence,revision:String(sequence).repeat(64),
 generated_at:new Date().toISOString(),alerts:[{...urgent,change_id:'CHG-123',popup_eligible:false},urgent]});}
const request=client=>new Request('https://edge.internal/alerts'+(client?'?client='+client:''));
const ids=response=>response.json().then(feed=>feed.alerts.map(row=>row.change_id));
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function harness(){
 let stored=snapshot(),reads=0,gate=null;
 const db={prepare(sql){let args=[];return {
  bind(...values){args=values;return this;},
  async all(){assert.match(sql,/SELECT payload,received_at/);reads++;
   const value={payload:JSON.stringify(stored),received_at:'immutable-receipt'};
   if(gate)await gate;return {results:[value]};},
  async run(){assert.match(sql,/INSERT INTO alert_feed/);stored=JSON.parse(args[3]);return {meta:{changes:1}};}
 };}};
 const edge=new HotEdge({storage:{}},{ALERTS_DB:db,ALERT_FEED_TOKEN:'test-only'});
 return {edge,reads:()=>reads,stored:()=>stored,block(){let release;gate=new Promise(resolve=>release=resolve);return ()=>{gate=null;release();};},
  post(seq){return edge.fetch(new Request('https://edge.internal/alerts',{method:'POST',
   headers:{Authorization:'Bearer test-only','Content-Type':'application/json'},body:JSON.stringify(snapshot(seq))}));}};
}
for(const first of ['alert-evidence-v1','legacy']){
 test(`real HotEdge cache isolation: ${first} followed by the other client within five seconds`,async()=>{
  const h=harness(),second=first==='legacy'?'alert-evidence-v1':'legacy';
  for(const client of [first,second,first,second])assert.deepEqual(await ids(await h.edge.fetch(request(client))),
   client==='alert-evidence-v1'?['CHG-123','CHG-456']:['CHG-456']);
  assert.equal(h.reads(),2,'one upstream read per normalized capability, warm variants stay cached');
  assert.deepEqual(await ids(await h.edge.fetch(request('unknown'))),['CHG-456']);
  assert.deepEqual(await ids(await h.edge.fetch(request())),['CHG-456']);assert.equal(h.reads(),2);
 });
 test(`real HotEdge in-flight isolation: ${first} starts before overlapping clients`,async()=>{
  const h=harness(),release=h.block();
  const a=h.edge.fetch(request(first));await flush();
  const b=h.edge.fetch(request(first==='legacy'?'alert-evidence-v1':'legacy'));
  const unknown=h.edge.fetch(request('unknown'));await flush();release();
  const [ra,rb,ru]=await Promise.all([a,b,unknown]);
  assert.deepEqual(await ids(ra),first==='legacy'?['CHG-456']:['CHG-123','CHG-456']);
  assert.deepEqual(await ids(rb),first==='legacy'?['CHG-123','CHG-456']:['CHG-456']);
  assert.deepEqual(await ids(ru),['CHG-456']);assert.equal(h.reads(),2);
 });
}
test('POST invalidates both variants; old in-flight reads cannot repopulate either cache',async()=>{
 const h=harness();
 await h.edge.fetch(request());await h.edge.fetch(request('alert-evidence-v1'));
 const original=JSON.stringify(h.stored());assert.equal((await h.post(2)).status,200);
 for(const client of ['', 'alert-evidence-v1'])assert.equal((await (await h.edge.fetch(request(client))).json()).sequence,2);
 assert.equal(h.reads(),4);
 // A completed POST must invalidate every variant and every old pending owner.
 h.edge.alertCache=null;const release=h.block();
 const pending=[h.edge.fetch(request()),h.edge.fetch(request('alert-evidence-v1'))];await flush();
 assert.equal((await h.post(3)).status,200);release();await Promise.all(pending);
 for(const client of ['', 'alert-evidence-v1']){
  const result=await (await h.edge.fetch(request(client))).json();assert.equal(result.sequence,3);
  assert.deepEqual(result.alerts.map(a=>a.change_id),client?['CHG-123','CHG-456']:['CHG-456']);
 }
 const full=await (await h.edge.fetch(request('alert-evidence-v1'))).json();
 assert.deepEqual(full.alerts,snapshot().alerts);assert.equal(full.received_at,'immutable-receipt');
 assert.deepEqual(JSON.parse(original).alerts,full.alerts,'projection preserves source and delivery fields');
});
test('each normalized capability keeps the five-second TTL independently',async()=>{
 const now=Date.now;let stamp=now();Date.now=()=>stamp;
 try {
  const h=harness();await h.edge.fetch(request());await h.edge.fetch(request('alert-evidence-v1'));
  stamp+=4999;await h.edge.fetch(request());await h.edge.fetch(request('alert-evidence-v1'));assert.equal(h.reads(),2);
  stamp+=1;await h.edge.fetch(request());await h.edge.fetch(request('alert-evidence-v1'));assert.equal(h.reads(),4);
 } finally {Date.now=now;}
});
