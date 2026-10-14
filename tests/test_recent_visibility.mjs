import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('../web/alerts_ui.js',import.meta.url),'utf8');
const app=fs.readFileSync(new URL('../web/app.js',import.meta.url),'utf8');
const health=fs.readFileSync(new URL('../web/checkpoint_health.js',import.meta.url),'utf8');
const NOW=Date.parse('2026-10-14T09:10:00Z');
const old={change_id:'CHG-oct13',created_at:'2026-10-13T12:00:00Z',detected_at:'2026-10-13T12:00:00Z',ticker:'TEST',priority:'P2',popup_eligible:false};
const urgent={...old,change_id:'CHG-urgent',popup_eligible:true,issuer_verified:true,confirmed_outcome:true,needs_review:false,
 semantic_version:'alert-evidence-v1',evidence_basis:'source_text',event_family:'fda_decision',event_outcome:'approval',priority:'P1'};
const feed=(alerts,revision='r1',at='2026-10-14T09:00:00Z')=>({schema:1,alerts,revision,generated_at:at,alert_config:{}});
function harness(initial=[old],saved=new Map()){
 const requests=[],shown=[],system=[],responses=[];
 class Clock extends Date{static now(){return NOW}}
 const storage={getItem:key=>saved.get(key)||null,setItem:(key,v)=>saved.set(key,v)};
 const c=vm.createContext({Date:Clock,URL,D:{alerts:initial,generated_at:'2026-10-14T08:00:00Z',alert_config:{feed_url:'https://clock.example/alerts'}},
  apiMode:false,document:{hidden:false},window:{localStorage:storage},navigator:{},render(){},
  fetch:async url=>{requests.push(url);const response=responses.shift();if(response instanceof Error)throw response;return {ok:true,json:async()=>response}}});
 vm.runInContext(source,c);
 c.showAlertPopup=rows=>shown.push(...rows);c.showSystemAlert=row=>system.push(row);
 return {c,requests,shown,system,responses,saved,poll:()=>c.checkForAlerts(),run:code=>vm.runInContext(code,c)};
}

test('Oct13 list-only alerts survive Oct14 polling, outage fallback and reload without popups',async()=>{
 const h=harness();h.responses.push(new Error('offline'),feed([old]));await h.poll();
 assert.deepEqual(h.requests,['https://clock.example/alerts?client=alert-evidence-v1','alerts-evidence-v1.json']);
 assert.equal(h.c.D.alerts[0].change_id,old.change_id);assert.equal(h.shown.length,0);
 h.responses.push(feed([old,urgent],'r2'));await h.poll();assert.equal(h.c.D.alerts.length,2);
 assert.deepEqual(h.shown.map(a=>a.change_id),[urgent.change_id]);assert.equal(h.system.length,1);
 h.responses.push(feed([old,urgent],'r3'));await h.poll();assert.equal(h.shown.length,1);
 const reload=harness(h.c.D.alerts,h.saved);reload.responses.push(feed([old,urgent],'r4'));await reload.poll();
 reload.responses.push(feed([old,urgent],'r5'));await reload.poll();assert.equal(reload.shown.length,0);
});

test('filtered legacy projection cannot erase a complete modern snapshot, even with the same revision',async()=>{
 const h=harness();h.responses.push(feed([old],'same'));await h.poll();
 h.responses.push(feed([],'same','2026-10-14T09:01:00Z'),feed([old],'same','2026-10-14T09:01:00Z'));await h.poll();
 assert.equal(h.c.D.alerts.length,1);assert.equal(h.requests.at(-1),'alerts-evidence-v1.json');
 h.responses.push(feed([],'filtered','2026-10-14T09:02:00Z'),feed([],'filtered','2026-10-14T09:02:00Z'));
 await assert.rejects(h.poll(),/Incomplete alert feed/);assert.equal(h.c.D.alerts.length,1);
});

test('failed, partial or malformed feed retains known list; expired rows can leave the seven-day feed',async()=>{
 for(const bad of [null,{...feed([]),truncated:true},feed([],'bad','invalid')]){
  const h=harness();h.responses.push(bad,bad);await assert.rejects(h.poll());assert.equal(h.c.D.alerts.length,1);
 }
 const h=harness([{...old,created_at:'2026-10-06T12:00:00Z'}]);h.responses.push(feed([]));await h.poll();assert.equal(h.c.D.alerts.length,0);
});

test('older independent feed cannot replace a newer complete snapshot on open-tab refresh',async()=>{
 const h=harness();h.responses.push(feed([old]));await h.poll();
 h.c.next={alerts:[old,urgent],generated_at:'2026-10-14T09:05:00Z',alert_config:{feed_url:'https://clock.example/alerts'}};
 h.run('preserveAlertFeed(next);D=next');assert.equal(h.c.D.alerts.length,2);
 h.responses.push(feed([old],'older','2026-10-14T09:02:00Z'),feed([old,urgent],'new','2026-10-14T09:06:00Z'));await h.poll();
 assert.equal(h.c.D.alerts.length,2);
});

test('every A5 eligibility field gates interrupts while all rows remain list-visible',async()=>{
 const h=harness([]);h.responses.push(feed([]));await h.poll();
 const invalid={popup_eligible:false,issuer_verified:false,confirmed_outcome:false,needs_review:true,
  semantic_version:'legacy',evidence_basis:'headline_only',priority:'P3',event_family:'milestone',event_outcome:'planned'};
 const rows=Object.entries(invalid).map(([key,value])=>({...urgent,change_id:key,[key]:value}));
 h.responses.push(feed([...rows,urgent],'r2'));await h.poll();assert.equal(h.c.D.alerts.length,rows.length+1);
 assert.deepEqual(h.shown.map(a=>a.change_id),[urgent.change_id]);
});

test('query parameters are retained and API also negotiates evidence capability',async()=>{
 for(const api of [false,true]){
  const h=harness([]);h.c.apiMode=api;h.c.D.alert_config.feed_url='https://clock.example/alerts?limit=50';
  h.responses.push(feed([]));await h.poll();
  assert.equal(h.requests[0],api?'/api/alerts?client=alert-evidence-v1':'https://clock.example/alerts?limit=50&client=alert-evidence-v1');
 }
});

test('zero displayed changes distinguishes absent, degraded and stale coverage',()=>{
 class Clock extends Date{static now(){return NOW}}
 const c=vm.createContext({Date:Clock,Intl,document:{},localStorage:{getItem:()=>null}});
 vm.runInContext(health,c);vm.runInContext(app.slice(0,app.indexOf("$('globalSearch').addEventListener")),c);
 c.data={changes:[],health:{status:'degraded'},checkpoint_at:'2026-10-14T09:00:00Z'};
 vm.runInContext('D=data',c);assert.match(c.changedCompact(),/כיסוי.*חלקי|כיסוי.*נכשל/);
 c.data.alerts=[];assert.match(c.emptyChangesHe('alerts'),/כיסוי.*חלקי|כיסוי.*נכשל/);
 c.data={health:{status:'ok'},checkpoint_at:'2026-10-14T09:00:00Z'};vm.runInContext('D=data',c);assert.match(c.changedCompact(),/לא זמין/);
 c.data={changes:[],health:{status:'ok'},checkpoint_at:'2026-10-13T09:00:00Z'};vm.runInContext('D=data',c);assert.match(c.changedCompact(),/ישנים/);
 c.data={changes:[],health:{status:'ok'},checkpoint_at:'2026-10-14T09:00:00Z'};vm.runInContext('D=data',c);assert.match(c.changedCompact(),/במקורות שנבדקו/);
});
