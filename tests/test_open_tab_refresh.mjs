import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../web/app.js',import.meta.url),'utf8');
const health=fs.readFileSync(new URL('../web/checkpoint_health.js',import.meta.url),'utf8');
const alerts=fs.readFileSync(new URL('../web/alerts_ui.js',import.meta.url),'utf8');
const NOW=Date.parse('2026-10-12T12:00:00Z');
const cp=(at='2026-10-12T11:50:00+00:00',id=1)=>({checkpoint_at:at,source_run_id:String(id),artifact_id:id,db_sha256:'a'.repeat(64)});
const data=(checkpoint=cp(),revision='same')=>({build:{snapshot_revision:revision},checkpoint_at:checkpoint.checkpoint_at,consumer_checkpoint:checkpoint,alerts:[]});
function harness(old=data(),{api=false}={}){
 let now=NOW;const elements=new Map(),listeners={},timers=[],requests=[],responses=[];
 class Clock extends Date{static now(){return now}}
 const document={hidden:false,addEventListener:(name,fn)=>listeners[name]=fn,getElementById:id=>{
  if(!elements.has(id))elements.set(id,{outerHTML:'',innerHTML:'',dataset:{checked:''},classList:{toggle(){}}});return elements.get(id);
 }};
 const c=vm.createContext({Date:Clock,Intl,URL,window:{},navigator:{},localStorage:{getItem:()=>null,setItem(){}},document,renderCount:0,messages:[],setInterval:(fn,ms)=>timers.push({fn,ms}),fetch:async url=>{
  requests.push(url);if(url==='alerts.json'||url==='/api/alerts')return {ok:true,json:async()=>({schema:1,revision:'independent',alerts:[],generated_at:'2026-10-12T11:59:00Z'})};if(!responses.length)throw Error('Unexpected fetch');return {ok:true,json:async()=>responses.shift()};
 }});
 // Execute actual application logic, not a reimplemented refresh algorithm.
 vm.runInContext(health,c);
 vm.runInContext(source.slice(0,source.indexOf("$('globalSearch').addEventListener")),c);
 vm.runInContext(alerts,c);
 vm.runInContext(source.slice(source.indexOf('setInterval(()=>')),c);
 c.initial=old;vm.runInContext(`D=initial;apiMode=${api};page='alerts';search='keep';radarFilters={type:'P3',cls:'REVIEW',verify:'VERIFIED'};render=()=>renderCount++;updateDataLabel=()=>{};toast=x=>messages.push(x)`,c);
 return {c,requests,responses,elements,listeners,timers,now:value=>now=value,
  run:code=>vm.runInContext(code,c),snapshot:()=>vm.runInContext('D',c),poll:()=>vm.runInContext('checkForUpdates()',c)};
}
const update=(checkpoint=cp(),revision='same')=>({schema:1,revision,checkpoint});
const advance=()=>cp('2026-10-12T11:55:00+00:00',2);
const flush=()=>new Promise(resolve=>setImmediate(resolve));

test('same revision, newer authoritative checkpoint refreshes an open tab',async()=>{
 const h=harness();h.responses.push(update(advance()),data(advance()));await h.poll();
 assert.equal(h.snapshot().checkpoint_at,advance().checkpoint_at);assert.deepEqual(h.requests,['updates.json','data.json']);
 assert.equal(h.run('page'),'alerts');assert.equal(h.run('search'),'keep');assert.equal(h.run('radarFilters.type'),'P3');
});
test('unchanged revision and identity avoids data download but updates clock banner',async()=>{
 const h=harness();h.responses.push(update());await h.poll();assert.deepEqual(h.requests,['updates.json']);assert.equal(h.c.renderCount,0);
 assert.match(h.elements.get('checkpointFreshness').outerHTML,/10 דקות/);
});
test('different semantic revision refreshes content against the same checkpoint',async()=>{
 const h=harness();h.responses.push(update(cp(),'changed'),data(cp(),'changed'));await h.poll();assert.equal(h.snapshot().build.snapshot_revision,'changed');
});
for(const issue of ['old-data','wrong-revision','missing-data-identity','conflicting-data-time'])test(`cross-deployment ${issue} retains loaded snapshot and retries next poll`,async()=>{
 const original=data(),h=harness(original);let next=data(advance());
 if(issue==='old-data')next=data();if(issue==='wrong-revision')next.build.snapshot_revision='wrong';
 if(issue==='missing-data-identity')delete next.consumer_checkpoint;if(issue==='conflicting-data-time')next.checkpoint_at=cp().checkpoint_at;
 h.responses.push(update(advance()),next);await assert.rejects(h.poll());assert.equal(h.snapshot(),original);
 h.responses.push(update(advance()),data(advance()));await h.poll();assert.equal(h.snapshot().checkpoint_at,advance().checkpoint_at);
});
for(const issue of ['older','future','invalid','missing-field','missing-metadata','conflicting-identity'])test(`rejects ${issue} checkpoint metadata before downloading data`,async()=>{
 const h=harness();let advertised=update(advance());
 if(issue==='older')advertised=update(cp('2026-10-12T11:40:00+00:00',3),'changed');
 if(issue==='future')advertised=update(cp('2026-10-12T12:01:00+00:00',3));
 if(issue==='invalid')advertised.checkpoint.checkpoint_at='2026-02-30T11:00:00+00:00';
 if(issue==='missing-field')delete advertised.checkpoint.db_sha256;
 if(issue==='missing-metadata')delete advertised.checkpoint;
 if(issue==='conflicting-identity')advertised=update({...cp(),artifact_id:3});
 h.responses.push(advertised);await assert.rejects(h.poll());assert.deepEqual(h.requests,['updates.json']);assert.equal(h.snapshot().checkpoint_at,cp().checkpoint_at);
});
test('independent feed and existing change_id deduplication survive checkpoint-only refresh',async()=>{
 const h=harness();h.c.feed={revision:'feed-independent',alerts:[{change_id:'CHG-keep'}],alert_config:{feed_url:'https://example.test/alerts'}};
 h.run('lastAlertFeed=feed');h.responses.push(update(advance()),data(advance()));await h.poll();
 assert.equal(h.snapshot().alerts,h.c.feed.alerts);assert.equal(h.snapshot().alert_config,h.c.feed.alert_config);
 let shown=0,notified=0;const values=new Map();h.c.storage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)};
 h.c.show=()=>shown++;h.c.system=()=>notified++;
 const notifier=h.run('createAlertNotifier({storage,show,system,now:()=>Date.now()})');
 await notifier.observe(h.c.feed.alerts,true);await notifier.observe(h.snapshot().alerts,false);assert.equal(shown,0);assert.equal(notified,0);
});
test('hidden tab skips minute polling and catches up on visibility restoration',async()=>{
 const h=harness();assert.equal(h.timers[0].ms,60000);h.c.document.hidden=true;h.timers[0].fn();await flush();assert.equal(h.requests.length,0);
 h.responses.push(update(advance()),data(advance()));h.c.document.hidden=false;h.listeners.visibilitychange();await flush();
 assert.equal(h.snapshot().checkpoint_at,advance().checkpoint_at);
});
test('unchanged snapshot crosses 45 minutes truthfully, without replacing loaded checkpoint',async()=>{
 const h=harness(data(cp('2026-10-12T11:16:00+00:00')));h.responses.push(update(cp('2026-10-12T11:16:00+00:00')));await h.poll();
 assert.match(h.elements.get('checkpointFreshness').outerHTML,/תמונת הניטור מעודכנת/);
 h.now(NOW+120000);h.responses.push(update(cp('2026-10-12T11:16:00+00:00')));await h.poll();
 assert.match(h.elements.get('checkpointFreshness').outerHTML,/נתונים ישנים — תמונת מצב בלבד/);assert.equal(h.snapshot().checkpoint_at,'2026-10-12T11:16:00+00:00');
});
test('manual static refresh uses the same checkpoint validation and preserves page',async()=>{
 const h=harness();h.responses.push(update(advance()),data(advance()));await h.run('refreshNow()');assert.equal(h.snapshot().checkpoint_at,advance().checkpoint_at);assert.equal(h.run('page'),'alerts');
});
for(const api of [false,true])test(`legacy ${api?'API':'static'} revision-only compatibility`,async()=>{
 const old={build:{snapshot_revision:'old'}},h=harness(old,{api});h.responses.push({revision:'old'});await h.poll();assert.equal(h.requests.length,1);
 h.responses.push({revision:'new'},{build:{snapshot_revision:'new'}});await h.poll();assert.equal(h.snapshot().build.snapshot_revision,'new');
 assert.equal(h.requests[2],api?'/api/radar':'data.json');
});
test('API initial unknown revision fetches and validates data rather than acknowledging a promise',async()=>{
 const h=harness({}, {api:true});h.responses.push({revision:'new'},{build:{snapshot_revision:'new'}});await h.poll();assert.equal(h.snapshot().build.snapshot_revision,'new');assert.equal(h.requests.length,2);
});
test('overlapping polls coalesce to a single bounded download sequence',async()=>{
 const h=harness();h.responses.push(update(advance()),data(advance()));await Promise.all([h.poll(),h.poll()]);assert.equal(h.requests.length,2);
});
