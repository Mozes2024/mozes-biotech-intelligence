import assert from 'node:assert/strict';
import {MonitorHeartbeat, DISPATCH_KEY} from './src/monitor-heartbeat.js';
import {HotEdge} from './src/hot-edge.js';
const now = Date.parse('2026-10-11T16:00:00Z'), repo = 'Mozes2024/mozes-biotech-intelligence';
const env = {GITHUB_TOKEN:'offline-token', GITHUB_REPO:repo};
const iso = n => new Date(n).toISOString();
const original = globalThis.fetch;
function harness({age=1500, active=false, ownerAge=60, bad=null, http=null, post=204}={}) {
  const memory = new Map(), posts = [];
  const storage = {get:async k=>memory.get(k),put:async(k,v)=>memory.set(k,v)};
  globalThis.fetch = async (url, options) => {
    assert.equal(new URL(url).origin, 'https://api.github.com');
    assert.equal(options.redirect, 'error');
    if (http) return new Response('{}',{status:http});
    if (options.method === 'POST') {
      assert.equal(memory.get(DISPATCH_KEY),now);
      posts.push(JSON.parse(options.body)); return new Response(null,{status:post});
    }
    const at=iso(now-age*1000);
    let value;
    if (url.includes('/artifacts?')) value={total_count:1,artifacts:[{name:'mozes-live-monitor',id:2,
      expired:false,size_in_bytes:100,digest:'sha256:'+'a'.repeat(64),created_at:at,
      workflow_run:{id:1,head_branch:'main',head_sha:'a'.repeat(40)}}]};
    else if (url.includes('/jobs?')) value={total_count:1,jobs:[{name:'monitor',status:'completed',steps:
      ['Bind durable checkpoint to monitor database','Run actions/upload-artifact@v4'].map(name=>
        ({name,status:'completed',conclusion:'success',completed_at:at}))}]};
    else if (url.includes('/workflows/')) {
      const status=new URL(url).searchParams.get('status');
      const wanted=active===true?'queued':active;
      const rows=active&&status===wanted?[{id:3,created_at:iso(now-ownerAge*1000),status,head_branch:'main',head_repository:{full_name:repo}}]:[];
      value={total_count:rows.length,workflow_runs:rows};
    } else value={id:1,head_branch:'main',head_repository:{full_name:repo},name:'lightweight-live-monitor',
      path:'.github/workflows/lightweight-monitor.yml',run_attempt:1,status:'completed',conclusion:'success',
      event:'workflow_dispatch',head_sha:'a'.repeat(40)};
    if (bad==='artifact_missing' && value.artifacts) value={total_count:0,artifacts:[]};
    if (bad==='artifact_digest' && value.artifacts) delete value.artifacts[0].digest;
    if (bad==='checkpoint_step' && value.jobs) value.jobs[0].steps[0].conclusion='skipped';
    if (bad==='rerun' && value.id===1) value.run_attempt=2;
    if (bad==='incomplete' && value.workflow_runs) value.total_count=101;
    return Response.json(value);
  };
  return {memory,storage,posts,heartbeat:new MonitorHeartbeat(storage,env)};
}
let count=0;
try {
  for (const [age,status,dispatches] of [[300,'FRESH',0],[1500,'DISPATCH_ACCEPTED_UNVERIFIED',1],
    [2701,'BLOCKED',0]]) {
    const h=harness({age}); assert.equal((await h.heartbeat.check(now)).status,status);
    assert.equal(h.posts.length,dispatches); count++;
  }
  let h;
  for (const active of ['queued','in_progress','waiting','pending','requested']) {
    h=harness({active}); assert.equal((await h.heartbeat.check(now)).status,'ACTIVE_MONITOR');
    assert.equal(h.posts.length,0); count++;
  }
  for (const active of ['queued','pending','waiting','in_progress','requested']) {
    h=harness({active,ownerAge:1200});
    const result=await h.heartbeat.check(now);
    assert.equal(result.status,'OVERDUE_OWNER');
    assert.equal(result.active_owner.run_id,3); assert.equal(result.active_owner.age_seconds,1200);
    assert.equal(result.category,'monitor_owner_overdue'); assert.ok(result.action_required);
    assert.equal(h.posts.length,0); count++;
  }
  h=harness({active:'queued',ownerAge:-1});
  assert.equal((await h.heartbeat.check(now)).category,'ambiguous_owner'); count++;
  h=harness({age:2701,active:'queued',ownerAge:3600});
  assert.equal((await h.heartbeat.check(now)).category,'outside_producer_restore_window');
  assert.equal(h.posts.length,0); count++;
  h=harness(); await Promise.all(Array.from({length:12},()=>h.heartbeat.check(now)));
  assert.deepEqual(h.posts,[{ref:'main',inputs:{priority_only:'true'}}]); count++;
  // A restart and an ambiguous rejected dispatch retain the durable reservation.
  h=harness({post:500}); assert.equal((await h.heartbeat.check(now)).category,'http_500');
  h.memory.delete('monitor_heartbeat');
  const restart=new MonitorHeartbeat(h.storage,env);
  assert.equal((await restart.check(now+1000)).status,'THROTTLED'); assert.equal(h.posts.length,1); count++;
  for (const bad of ['artifact_missing','artifact_digest','checkpoint_step','rerun','incomplete']) {
    h=harness({bad}); assert.equal((await h.heartbeat.check(now)).status,'BLOCKED');
    assert.equal(h.posts.length,0); count++;
  }
  for (const http of [401,403,429,500,503]) {
    h=harness({http}); assert.equal((await h.heartbeat.check(now)).category,`http_${http}`);
    assert.equal(h.posts.length,0); count++;
  }
  h=harness(); assert.equal((await new MonitorHeartbeat(h.storage,{}).check(now)).category,'token_missing'); count++;
  h=harness(); globalThis.fetch=async()=>{throw Error('secret must not escape');};
  assert.equal((await h.heartbeat.check(now)).category,'network_or_timeout'); count++;
  h=harness(); h.memory.set(DISPATCH_KEY,now); assert.equal((await h.heartbeat.check(now)).status,'THROTTLED');count++;
  // Dedicated alarm slots reserve all GitHub headroom, even with sustained source load.
  h=harness({active:'queued'});
  h.storage.getAlarm=async()=>now+120000; h.storage.setAlarm=async()=>{};
  const edge=new HotEdge({storage:h.storage},{...env,EDGE_INTERVAL_SECONDS:'120'});
  await edge.ready;
  let polls=0;
  edge.pollSec=async()=>{polls++;edge.sourceFetches=25;}; edge.pollWire=async()=>{}; edge.analyzePending=async()=>({processed:1});
  edge.flushPending=async()=>{};
  edge.reconcileGaps=async()=>{}; edge.checkpoint=async()=>{};
  const originalNow=Date.now; let clock=now, checks=0; const checked=[];
  Date.now=()=>clock;
  h.memory.set(DISPATCH_KEY,now);
  edge.monitorHeartbeat.check=async at=>{checks++;checked.push(at);return new MonitorHeartbeat(h.storage,env).check(at);};
  try {
    await Promise.all([edge.alarm(),edge.alarm()]);
    assert.equal(checks,1); assert.equal(polls,0); assert.equal(h.posts.length,0);
    assert.equal(h.memory.get('monitor_heartbeat').status,'ACTIVE_MONITOR'); count++;
    for(let cycle=1;cycle<=30;cycle++) {clock=now+cycle*120000;await edge.alarm();}
    assert.ok(checks>=6); assert.ok(polls>=20);
    assert.ok(checked.slice(1).every((at,index)=>at-checked[index]<=600000));
    assert.equal(h.posts.length,0); // Aged evidence blocks after 45m; no recovery inputs.
    assert.equal(h.memory.get('monitor_heartbeat').category,'outside_producer_restore_window'); count++;
  } finally {Date.now=originalNow;}
  console.log(`monitor heartbeat: ${count} checks passed`);
} finally {globalThis.fetch=original;}
