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
  requests.push(url);if(url==='alerts-evidence-v1.json'||url==='/api/alerts?client=alert-evidence-v1')return {ok:true,json:async()=>({schema:1,revision:'independent',alerts:[],generated_at:'2026-10-12T11:59:00Z'})};if(!responses.length)throw Error('Unexpected fetch');return {ok:true,json:async()=>responses.shift()};
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
const advance=()=>cp('2026-10-12T14:55:00+03:00',2);
const flush=()=>new Promise(resolve=>setImmediate(resolve));

const production=()=>({...cp('2026-10-12T12:58:40+03:00'),source_run_id:'38233197326',artifact_id:11739482359});
test('exact production offset checkpoint refreshes and displays its real age',async()=>{
 const old=production();old.checkpoint_at='2026-10-12T12:57:40+03:00';const h=harness(data(old));
 h.now(Date.parse('2026-10-12T10:00:40Z'));h.responses.push(update(production()),data(production()));await h.poll();
 assert.equal(h.snapshot().checkpoint_at,production().checkpoint_at);
 assert.match(h.elements.get('checkpointFreshness').outerHTML,/2 דקות/);
 assert.match(h.elements.get('checkpointFreshness').outerHTML,/12:58:40\+03:00/);
});

for(const at of ['2026-10-12T09:58:40Z','2026-10-12T09:58:40+00:00','2026-10-12T12:58:40+03:00','2026-10-12T05:58:40-04:00','2026-10-12T04:58:40-05:00'])test(`equivalent instant ${at} avoids downloads and false identity conflicts`,async()=>{
 const h=harness(data(production()));h.responses.push(update({...production(),checkpoint_at:at}));await h.poll();
 assert.deepEqual(h.requests,['updates.json']);assert.equal(h.snapshot().checkpoint_at,production().checkpoint_at);
 h.c.at=at;assert.equal(h.run('checkpointTime(at)'),Date.parse('2026-10-12T09:58:40Z'));
 assert.equal(h.run('MozesCheckpointHealth({checkpoint_at:at}).age_minutes'),121);
});

for(const [at,utc] of [
 ['2025-01-01T00:30:00+03:00','2024-12-31T21:30:00Z'],
 ['2024-12-31T23:30:00-05:00','2025-01-01T04:30:00Z'],
 ['2024-02-29T00:15:00+03:00','2024-02-28T21:15:00Z'],
 ['2000-02-29T23:59:59.123456-04:00','2000-03-01T03:59:59.123Z'],
 ['0099-01-01T00:00:00Z','0099-01-01T00:00:00Z'],
 ['2026-01-01T00:00:00+23:59','2025-12-31T00:01:00Z']
])test(`calendar and boundary conversion ${at}`,()=>{
 const h=harness();h.c.at=at;assert.equal(h.run('checkpointTime(at)'),Date.parse(utc));
});

for(const at of [
 '2026-02-29T10:00:00+03:00','1900-02-29T10:00:00Z','2024-02-30T10:00:00Z',
 '2026-04-31T10:00:00Z','2026-00-01T10:00:00Z','2026-13-01T10:00:00Z','2026-01-00T10:00:00Z',
 '2026-01-01T24:00:00Z','2026-01-01T10:60:00Z','2026-01-01T10:00:60Z',
 '2026-01-01T10:00:00+24:00','2026-01-01T10:00:00-04:60','2026-01-01T10:00:00+0300',
 '2026-01-01T10:00:00','2026-01-01','2026-01-01T10:00:00Zjunk',
 '2026-10-12T15:00:01+03:00','2026-10-12T08:00:01-04:00'
])test(`both consumers reject invalid or future ${at}`,async()=>{
 const h=harness();h.c.at=at;assert.throws(()=>h.run('checkpointTime(at)'),/Invalid checkpoint time/);
 const health=h.run('MozesCheckpointHealth({checkpoint_at:at})');assert.equal(health.age_minutes,null);assert.equal(health.stale,true);
 h.responses.push(update(cp(at,2)));await assert.rejects(h.poll(),/Invalid checkpoint time/);assert.equal(h.requests.length,1);
});

test('matching normalized update and data times permit an in-place refresh',async()=>{
 const h=harness(data(cp('2026-10-12T12:57:40+03:00')));
 const advertised=production(),downloaded={...advertised,checkpoint_at:'2026-10-12T09:58:40Z'};
 const next=data(downloaded);next.checkpoint_at=advertised.checkpoint_at;
 h.responses.push(update(advertised),next);await h.poll();assert.equal(h.snapshot(),next);
 assert.equal(h.run('page'),'alerts');assert.equal(h.run('search'),'keep');assert.equal(h.run('radarFilters.type'),'P3');
});

for(const field of ['source_run_id','artifact_id','db_sha256'])test(`same instant with conflicting ${field} stays rejected`,async()=>{
 const original=data(production()),h=harness(original);
 const conflicting={...production(),checkpoint_at:'2026-10-12T09:58:40Z',[field]:field==='artifact_id'?11739482360:field==='source_run_id'?'38233197327':'b'.repeat(64)};
 h.responses.push(update(conflicting));await assert.rejects(h.poll(),/conflict/);assert.equal(h.snapshot(),original);assert.equal(h.requests.length,1);
});

test('offset older checkpoint is rejected by UTC instant rather than wall time',async()=>{
 const h=harness(data(production()));h.responses.push(update(cp('2026-10-12T14:00:00+05:00',2)));
 await assert.rejects(h.poll(),/downgrade/);assert.equal(h.requests.length,1);
});

test('offset cross-deployment mismatch keeps the existing snapshot',async()=>{
 const original=data(cp('2026-10-12T12:57:40+03:00')),h=harness(original);
 h.responses.push(update(production()),data({...production(),checkpoint_at:'2026-10-12T05:58:39-04:00'}));
 await assert.rejects(h.poll(),/mismatch/);assert.equal(h.snapshot(),original);
});

for(const field of ['source_run_id','artifact_id','db_sha256'])test(`downloaded conflicting ${field} cannot replace advertised offset identity`,async()=>{
 const original=data(cp('2026-10-12T12:57:40+03:00')),h=harness(original);
 const downloaded={...production(),checkpoint_at:'2026-10-12T09:58:40Z',[field]:field==='artifact_id'?11739482360:field==='source_run_id'?'38233197327':'b'.repeat(64)};
 h.responses.push(update(production()),data(downloaded));await assert.rejects(h.poll(),/mismatch/);assert.equal(h.snapshot(),original);
});

test('production offset banner ages across the 45-minute threshold',async()=>{
 const h=harness(data(production()));h.now(Date.parse('2026-10-12T10:43:40Z'));
 h.responses.push(update({...production(),checkpoint_at:'2026-10-12T09:58:40Z'}));await h.poll();
 assert.match(h.elements.get('checkpointFreshness').outerHTML,/תמונת הניטור מעודכנת/);
 h.now(Date.parse('2026-10-12T10:43:40.001Z'));h.responses.push(update(production()));await h.poll();
 assert.match(h.elements.get('checkpointFreshness').outerHTML,/נתונים ישנים/);assert.equal(h.requests.length,2);
 assert.equal(h.snapshot().checkpoint_at,production().checkpoint_at);
});

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
 const h=harness();h.c.feed={schema:1,generated_at:'2026-10-12T11:59:00Z',revision:'feed-independent',alerts:[{change_id:'CHG-keep'}],alert_config:{feed_url:'https://example.test/alerts'}};
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

const researchEvent=(overrides={})=>({id:'VERIFY-1',ticker:'AAA',company:'Example',program:'Upcoming catalyst',type:'P3_TOPLINE',
 state:{verification_state:'VERIFIED'},security:{status:'ACTIVE',tradable:true},impact:{score:72},
 classification:{class:'INSUFFICIENT_INFORMATION',actionable:false},recommendation:{status:'INSUFFICIENT_INFORMATION',label_he:'חסר מידע'},
 evidence:{score:91,information_status:'PARTIAL',missing_requirements:{clinical:['design'],regulatory:[],financial:['source_verified_current_runway']}},
 date:{window:{start:'2026-12-01',end:'2026-12-31'}},risk_flags:[],
 sources:[{source_type:'company_ir',url:'https://example.test/catalyst',supports_date:true,published_at:'2026-09-30',retrieved_at:'2026-10-01T10:00:00Z'}],...overrides});
const researchHarness=(rows=[researchEvent()])=>harness({...data(),today:'2026-10-12',live:rows,validation:{runup:{satisfied:false},hold_through:{satisfied:false}}});

test('missing evidence remains visible in a separate research section without an investment badge or score',()=>{
 const h=researchHarness(),before=JSON.stringify(h.snapshot());const html=h.run('dashboard()');
 assert.match(html,/עדיפות למחקר \/ אימות/);assert.match(html,/זו אינה המלצת השקעה/);
 const section=html.slice(html.indexOf('<section class="panel simple-panel research-priority">'));
 assert.match(section,/data-event="VERIFY-1"/);assert.match(section,/חסר מידע/);assert.match(section,/ראיות קליניות/);
 assert.doesNotMatch(section,/מועמדת להשקעה|חוזק הראיות|91|focus-card advanced|focus-card strong/);
 assert.equal(JSON.stringify(h.snapshot()),before);assert.equal(h.requests.length,0);
});

for(const [name,override] of [
 ['unverified',{state:{verification_state:'QUARANTINED'}}],['untradable',{security:{status:'DELISTED',tradable:false}}],
 ['resolved',{classification:{class:'RESOLVED'}}],['stale',{date:{window:{start:'2026-09-01',end:'2026-09-30'}}}],
 ['unscheduled',{date:{window:null}}],['no-impact',{impact:{score:0}}],['nonfinite-impact',{impact:{score:Infinity}}],
 ['informational',{type:'FILING'}],['observed-risk',{risk_flags:[{sev:'high',code:'short_cash_runway'}]}],
 ['explicit-negative',{classification:{class:'AVOID_BINARY'},recommendation:{status:'NOT_NOW'}}],
 ['impossible-calendar',{date:{window:{start:'2026-11-01',end:'2026-11-31'}}}],
 ['reversed-window',{date:{window:{start:'2026-12-31',end:'2026-12-01'}}}],
])test(`research priority excludes ${name} events`,()=>{
 const h=researchHarness([researchEvent(override)]);assert.equal(h.run('researchPriorityRows(D.live).length'),0);
 assert.doesNotMatch(h.run('dashboard()'),/research-priority/);
});

for(const [name,override] of [
 ['secondary',{source_type:'aggregator'}],['no-date-support',{supports_date:false}],['missing-url',{url:null}],
 ['future-publication',{published_at:'2026-10-13'}],['same-day-publication',{published_at:'2026-10-12'}],
 ['future-capture',{retrieved_at:'2026-10-13T10:00:00Z'}],['missing-capture',{retrieved_at:null}],
])test(`research priority requires source-known date verification: ${name}`,()=>{
 const event=researchEvent();event.sources=[{...event.sources[0],...override}];
 assert.equal(researchHarness([event]).run('researchPriorityRows(D.live).length'),0);
});

test('priority ordering uses existing impact and dates, is capped at six, and preserves the main WATCH shortlist',()=>{
 const rows=Array.from({length:8},(_,i)=>researchEvent({id:`VERIFY-${i}`,impact:{score:70+i}}));
 rows.push(researchEvent({id:'WATCH-keep',type:'CONFERENCE',classification:{class:'WATCH'},recommendation:{status:'WATCH'}}));
 const h=researchHarness(rows),before=JSON.stringify(h.snapshot());
 assert.deepEqual(Array.from(h.run('researchPriorityRows(D.live).map(x=>x.id)')),['VERIFY-7','VERIFY-6','VERIFY-5','VERIFY-4','VERIFY-3','VERIFY-2']);
 const html=h.run('dashboard()'),split=html.indexOf('<section class="panel simple-panel research-priority">');
 assert.match(html.slice(0,split),/data-event="WATCH-keep"/);assert.doesNotMatch(html.slice(0,split),/data-event="VERIFY-/);
 assert.equal(JSON.stringify(h.snapshot()),before);
});

for(const [published_at,expected] of [['2026-08',1],['2025',1],['2026-10',0],['2026',0],['2026-13',0]])test(`coarse source publication ${published_at} uses conservative period end`,()=>{
 const event=researchEvent();event.sources[0].published_at=published_at;
 assert.equal(researchHarness([event]).run('researchPriorityRows(D.live).length'),expected);
});
