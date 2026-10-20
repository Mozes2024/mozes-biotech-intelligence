import test from 'node:test';
import assert from 'node:assert/strict';
import {HotEdge} from './src/hot-edge.js';

const now = Date.parse('2026-10-20T12:00:00Z');
function fixture(age, enabled='1') {
  const store = new Map();
  const self = {env:{GITHUB_TOKEN:'fixture',GITHUB_REPO:'Owner/Repo',EDGE_MONITOR_WATCHDOG_ENABLED:enabled},
    sourceState:{}, state:{storage:{get:async k=>store.get(k),put:async(k,v)=>store.set(k,v)}},
    db:{prepare:()=>({first:async()=>({generated_at:new Date(now-age*60000).toISOString()})})},
    success:(h,n)=>{h.sources[n]={status:'OK'};}, failure:(h,n)=>{h.sources[n]={status:'FAILED'};}};
  return {self,store,health:{sources:{}}};
}

test('quiet sources still request one bounded reconciliation before the restore cutoff', async t=>{
  t.mock.method(Date,'now',()=>now);
  let requests=0;
  t.mock.method(globalThis,'fetch',async(_url,options)=>{
    requests++;
    assert.deepEqual(JSON.parse(options.body),{ref:'main',inputs:{priority_only:'true'}});
    return new Response(null,{status:204});
  });
  const {self,health}=fixture(25);
  await HotEdge.prototype.reconcileGaps.call(self,health);
  await HotEdge.prototype.reconcileGaps.call(self,health);
  assert.equal(requests,1);
  assert.equal(health.monitor_liveness.status,'THROTTLED_UNVERIFIED');
});

test('stale, unknown and future feed hints never authorize recovery', async t=>{
  t.mock.method(Date,'now',()=>now);
  t.mock.method(globalThis,'fetch',()=>{throw Error('unexpected dispatch');});
  for(const age of [46,-5]) {
    const {self,health}=fixture(age);
    await HotEdge.prototype.reconcileGaps.call(self,health);
    assert.ok(['RECOVERY_REQUIRED','UNKNOWN'].includes(health.monitor_liveness.status));
  }
});

test('watchdog is inactive by default and ambiguous dispatch is durably throttled', async t=>{
  t.mock.method(Date,'now',()=>now);
  const disabled=fixture(25,undefined);
  delete disabled.self.env.EDGE_MONITOR_WATCHDOG_ENABLED;
  await HotEdge.prototype.reconcileGaps.call(disabled.self,disabled.health);
  let requests=0;
  t.mock.method(globalThis,'fetch',async()=>{requests++;throw Error('unknown acceptance');});
  const {self,health,store}=fixture(25);
  await HotEdge.prototype.reconcileGaps.call(self,health);
  await HotEdge.prototype.reconcileGaps.call(self,health);
  assert.equal(requests,1);
  assert.equal(store.get('reconciliation_requested_at'),now);
});
