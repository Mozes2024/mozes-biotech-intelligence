import assert from 'node:assert/strict';
import {HotEdge} from './src/hot-edge.js';
import {state} from './test-db.mjs';

const originalFetch=globalThis.fetch, originalNow=Date.now;
let clock=Date.parse('2026-10-20T20:00:00Z');
Date.now=()=>clock;
try {
  const s=state(), env={EDGE_INTERVAL_SECONDS:'120',EDGE_MONITOR_HEARTBEAT_ENABLED:'1',GITHUB_TOKEN:'offline'};
  const edge=new HotEdge(s,env);
  let polls=0, checks=0;
  edge.pollSec=async()=>{polls++;};edge.pollWire=async()=>{polls++;};
  edge.analyzePending=async()=>({failed:0});edge.flushPending=async()=>{};
  edge.monitorHeartbeat.check=async()=>{checks++;await s.storage.put('monitor_heartbeat',{status:'DISPATCH_ACCEPTED_UNVERIFIED',next_check_at:clock+600000});};
  globalThis.fetch=()=>{throw Error('No real network in Stage A integration');};
  await Promise.all([edge.alarm(),edge.alarm()]);
  assert.equal(checks,1);assert.equal(polls,0);
  assert.equal(await s.storage.getAlarm(),clock+1000);
  clock+=1000;await edge.alarm();assert.equal(polls,4);
  for(let n=0;n<4;n++){clock+=120000;await edge.alarm();}
  assert.equal(polls,20);assert.equal(checks,1);
  clock+=120000;await edge.alarm();assert.equal(checks,2);
  const health=await (await edge.fetch(new Request('https://edge.internal/health'))).json();
  assert.equal(health.monitor_heartbeat.status,'DISPATCH_ACCEPTED_UNVERIFIED');
  env.EDGE_MONITOR_HEARTBEAT_ENABLED='0';
  await edge.alarm();assert.equal(polls,24);assert.equal(checks,2);
  assert.equal((await (await edge.fetch(new Request('https://edge.internal/health'))).json()).monitor_heartbeat.status,'DISABLED');
  assert.equal(await s.storage.getAlarm(),clock+120000);
  console.log('Stage A continuity: serialized liveness, bounded polls, disabled rollback passed');
} finally {globalThis.fetch=originalFetch;Date.now=originalNow;}
