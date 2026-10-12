import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Execute every production script, including startup and the final coverage wrapper.
// Only the browser DOM, clock, timers and network boundary are doubled.
const web=new URL('../web/',import.meta.url);
const index=fs.readFileSync(new URL('index.html',web),'utf8');
const scripts=Array.from(index.matchAll(/<script\b[^>]*\bsrc="([^"]+)"[^>]*>/g),m=>m[1]);
const fixture=JSON.parse(fs.readFileSync(new URL('fixtures/dashboard_pr30.json',import.meta.url),'utf8'));
const RESEARCH=['KOD-PEAK-PA1','AUTO-HROW-NCT07832552','AUTO-MRNA-NCT06033261',
 'AUTO-ALKS-NCT06843590','AUTO-BHVN-NCT06309966','AUTO-CERS-NCT03037164'];
const WATCH=['KOD-BLA','AUTO-ALGS-NCT06963710','AUTO-BLTE-NCT05244304',
 'AUTO-ABVX-NCT05507203','AUTO-DNLI-NCT06075537','AUTO-MIRM-NCT04663308'];
const clone=value=>JSON.parse(JSON.stringify(value));
const checkpoint=(id=1)=>({checkpoint_at:`2026-10-12T12:${id===1?'40':'41'}:00+00:00`,
 source_run_id:String(id),artifact_id:id,db_sha256:String(id).repeat(64)});
function payload(){const cp=checkpoint();return {...clone(fixture),generated_at:cp.checkpoint_at,build:{snapshot_revision:'same'},
 checkpoint_at:cp.checkpoint_at,consumer_checkpoint:cp};}
const flush=()=>new Promise(resolve=>setImmediate(resolve));

test('changed dashboard assets use one new compatible version while checkpoint health keeps its version',()=>{
 const versions=new Map(scripts.map(src=>{const url=new URL(src,'https://example.invalid/');return [url.pathname,url.searchParams.get('v')];}));
 const version=versions.get('/app.js');assert.ok(version);assert.notEqual(version,'checkpoint-timezone-v1');
 assert.equal(versions.get('/live_intelligence_ui.js'),version);
 assert.equal(versions.get('/checkpoint_health.js'),'checkpoint-timezone-v1');
});

async function harness(initial=payload()){
 const elements=new Map(),storage=new Map(),requests=[],responses=[],notifications=[],timers=[];
 let renders=0,feed={schema:1,revision:'baseline',generated_at:initial.generated_at,
  alerts:clone(initial.alerts),alert_config:initial.alert_config};
 function element(id){if(!elements.has(id)){
  let html='';const node={hidden:true,dataset:{},textContent:'',outerHTML:'',
   classList:{add(){},remove(){},toggle(){}},addEventListener(){},
   querySelectorAll:()=>[],querySelector:()=>({})};
  Object.defineProperty(node,'innerHTML',{get:()=>html,set:value=>{html=value;if(id==='main')renders++;}});
  elements.set(id,node);
 }return elements.get(id);}
 class Clock extends Date{static now(){return Date.parse('2026-10-12T13:00:00Z')}}
 class Notification{static permission='granted';constructor(title,options){notifications.push({title,options});}}
 const localStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)};
 const c=vm.createContext({Date:Clock,Intl,URL,Notification,navigator:{},location:{hash:''},
  window:{localStorage,Notification},localStorage,document:{hidden:false,addEventListener(){},
   querySelectorAll:()=>[],getElementById:element},
  setInterval:(fn,ms)=>timers.push({fn,ms}),setTimeout:(fn,ms)=>timers.push({fn,ms}),
  fetch:async url=>{requests.push(url);
   if(url==='/api/radar')return {ok:false,status:404};
   if(url==='alerts.json')return {ok:true,json:async()=>clone(feed)};
   if(responses.length)return {ok:true,json:async()=>responses.shift()};
   if(url==='data.json')return {ok:true,json:async()=>clone(initial)};
   throw Error(`Unexpected network request: ${url}`);
  }});
 for(const src of scripts){const path=src.split('?')[0];
  vm.runInContext(fs.readFileSync(new URL(path,web),'utf8'),c,{filename:src});
 }
 await flush();await flush();
 return {run:code=>vm.runInContext(code,c),html:()=>element('main').innerHTML,
  popup:()=>element('alertPopups'),renders:()=>renders,elements,requests,responses,notifications,
  setFeed:value=>{feed=value;},snapshot:()=>vm.runInContext('D',c),timers};
}
const ids=html=>Array.from(html.matchAll(/<article class="focus-card [^"]+" data-event="([^"]+)"/g),m=>m[1]);
function research(html){return html.match(/<section class="[^"]*research-priority"[\s\S]*?<\/section>/)?.[0]||'';}
function assertComposed(h){
 const html=h.html(),section=research(html);
 assert.equal((html.match(/<h3>עדיפות למחקר \/ אימות<\/h3>/g)||[]).length,1);
 assert.deepEqual(ids(section),RESEARCH);
 assert.deepEqual(ids(html).filter(id=>!RESEARCH.includes(id)),WATCH);
 assert.equal((section.match(/>חסר מידע<\/span>/g)||[]).length,6);
 assert.match(section,/זו אינה המלצת השקעה/);
 assert.doesNotMatch(section,/חוזק הראיות|class="score"|מועמדת להשקעה|שווה מחקר/);
 assert.equal(new Set(ids(html)).size,12);
 assert.ok(html.indexOf('research-priority')>html.indexOf(`data-event="${WATCH.at(-1)}"`));
 assert.ok(html.indexOf('research-priority')<html.indexOf('<h3>מה השתנה?</h3>'));
}

test('actual production script chain renders six research cards and original deduplicated WATCH cards',async()=>{
 const h=await harness();assert.equal(h.run('researchPriorityRows(D.live).length'),6);assertComposed(h);
 // KOD has both a WATCH and a research event: ticker dedup must not remove the latter.
 assert.ok(h.html().includes('data-event="KOD-BLA"')&&research(h.html()).includes('data-event="KOD-PEAK-PA1"'));
 assert.equal(h.renders(),2); // application startup plus independent alert feed baseline
});

test('Live Intelligence, financial/provenance wrappers and secondary sections remain composed',async()=>{
 const h=await harness(),html=h.html();
 for(const text of ['תשומת לב שוק','מחיר לא טרי','הטריגר הבא','מקורות ואסמכתאות',
  'שרשרת קטליזטורים','אומדן מסלול מזומנים','מקור ואימות','סטטוס מערכת',
  'אבני דרך קליניות קרובות','כיסוי מקורות','מה השתנה?','שערי המסחר האמפיריים עדיין נעולים'])assert.ok(html.includes(text),text);
 assert.equal((html.match(/<h3>התראות אחרונות<\/h3>/g)||[]).length,1);
 assert.equal((html.match(/<h3>מה השתנה\?<\/h3>/g)||[]).length,1);
 assert.equal((html.match(/id="sourceFreshness"/g)||[]).length,1);
 assert.equal(h.popup().hidden,true);assert.equal(h.notifications.length,0);
 assert.deepEqual(h.snapshot().validation,fixture.validation);
});

test('unsafe research events are excluded by the unchanged selector through the final dashboard',async()=>{
 const data=payload(),base=data.live.find(a=>a.id===RESEARCH[0]);
 const cases={critical:a=>a.risk_flags=[{sev:'critical'}],high:a=>a.risk_flags=[{sev:'high'}],
  acquired:a=>a.security={status:'ACQUIRED',tradable:false},
  stale:a=>a.date.window.end='2026-10-11',unverified:a=>a.state.verification_state='DISCOVERY_ONLY',
  futureSource:a=>a.sources.forEach(s=>s.retrieved_at='2026-10-13'),
  missingPrimarySource:a=>a.sources=[]};
 for(const [name,change] of Object.entries(cases)){
  const a=clone(base);a.id=`excluded-${name}`;a.ticker=`EX-${name}`;a.impact.score=100;change(a);data.live.push(a);
 }
 const h=await harness(data);assertComposed(h);
 for(const name of Object.keys(cases))assert.ok(!ids(research(h.html())).includes(`excluded-${name}`));
});

test('empty research population omits the section and eligible overflow remains capped at six',async()=>{
 const empty=payload();empty.live=empty.live.filter(a=>!RESEARCH.includes(a.id));
 const first=await harness(empty);assert.equal(research(first.html()),'');assert.deepEqual(ids(first.html()),WATCH);
 const full=payload(),extra=clone(full.live.find(a=>a.id===RESEARCH[0]));extra.id='overflow';extra.ticker='EXTRA';extra.impact.score=1;full.live.push(extra);
 assertComposed(await harness(full));
});

test('same revision and newer offset checkpoint refresh the full composed dashboard without a reload',async()=>{
 const h=await harness(),before=h.renders();assertComposed(h);
 const cp=checkpoint(2),next=payload();next.consumer_checkpoint={...cp,checkpoint_at:'2026-10-12T15:41:00+03:00'};
 next.checkpoint_at=next.consumer_checkpoint.checkpoint_at;
 next.live.find(a=>a.id===RESEARCH[0]).program='Updated research program';
 next.alerts=[]; // independent alerts must survive replacement of the large payload
 h.responses.push({schema:1,revision:'same',checkpoint:cp},next);
 await h.run('checkForUpdates()');assert.equal(h.renders(),before+1);assertComposed(h);
 assert.match(research(h.html()),/Updated research program/);
 assert.equal(h.snapshot().alerts.length,fixture.alerts.length);
 assert.equal(h.snapshot().consumer_checkpoint.artifact_id,2);
 assert.equal(h.run('MozesCheckpointHealth(D).age_minutes'),19);
 assert.equal(h.requests.filter(url=>url==='data.json').length,2);
 assert.equal(h.popup().hidden,true);assert.equal(h.notifications.length,0);
 // Equivalent instant, unchanged revision: neither download nor render again.
 h.responses.push({schema:1,revision:'same',checkpoint:{...cp,checkpoint_at:'2026-10-12T15:41:00+03:00'}});
 await h.run('checkForUpdates()');assert.equal(h.renders(),before+1);
});

test('independent change_id dedup survives repeated alert polls and composed dashboard rerenders',async()=>{
 const h=await harness(),base=clone(fixture.alerts[0]);base.change_id='integration-new-change';
 const feed={schema:1,revision:'new-feed',generated_at:'2026-10-12T12:50:00Z',alerts:[base],alert_config:{enabled_channels:[]}};
 h.setFeed(feed);await h.run('checkForAlerts()');assert.equal(h.notifications.length,1);
 const popup=h.popup().innerHTML;assert.equal(h.popup().hidden,false);assertComposed(h);
 h.setFeed({...feed,revision:'same-change-other-revision'});await h.run('checkForAlerts()');h.run('render()');
 assert.equal(h.notifications.length,1);assert.equal(h.popup().innerHTML,popup);
 assert.equal((h.html().match(/<h3>התראות אחרונות<\/h3>/g)||[]).length,1);assertComposed(h);
});
