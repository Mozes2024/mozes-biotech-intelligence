import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {database,state} from './test-db.mjs';
import {HotEdge,parseWire} from './src/hot-edge.js';
import {budgetMode,Usage,instrumentDatabase} from './src/edge-runtime.js';
import worker,{syncIssuers} from './src/index.js';

const issuer={cik:'123',ticker:'ZZZZ',company:'Novel Bio',confidence:.99,source:'SEC-v2C-equity'};
const originalFetch=globalThis.fetch,originalNow=Date.now;
const rss=(n,offset=0,extra='')=>'<rss><channel>'+Array.from({length:n},(_,i)=>`<item><guid>item-${i+offset}</guid><title>Novel Bio Phase 2 results ${i+offset}</title><link>https://www.businesswire.com/news/${i+offset}</link><description>Novel Bio (NASDAQ: ZZZZ) met its primary endpoint. ${extra}</description><pubDate>Fri, 09 Oct 2026 08:00:00 GMT</pubDate></item>`).join('')+'</channel></rss>';
const atom=(n,offset=0)=>'<feed>'+Array.from({length:n},(_,i)=>{
  const accession=`0000000123-26-${String(i+offset).padStart(6,'0')}`;
  return `<entry><title>8-K - Novel Bio (123) (Filer)</title><link href="https://www.sec.gov/Archives/edgar/data/123/${accession.replaceAll('-','')}/filing-index.htm"/><id>accession-number=${accession}</id><updated>2026-10-09T08:00:00Z</updated></entry>`;
}).join('')+'</feed>';
try {
  const db=database(),s=state(),edge=new HotEdge(s,{ALERTS_DB:db,SEC_USER_AGENT:'test'});
  await Promise.all([edge.issuers(),edge.issuers(),edge.issuers()]);assert.equal(db.counts.queries,1);
  await edge.issuers();assert.equal(db.counts.queries,1);
  edge.issuerCache.expires=0;await edge.issuers();assert.equal(db.counts.queries,2);
  assert.equal((await edge.sync([issuer])).changed,0);
  const before={...db.counts};await edge.sync([issuer]);assert.deepEqual(db.counts,before);
  const changed={...issuer,ticker:'YYYY'};assert.equal((await edge.sync([changed])).changed,1);
  assert.equal(edge.issuerCache,null);assert.equal((await edge.issuers())[0].ticker,'YYYY');
  const restarted=new HotEdge(s,{ALERTS_DB:db});await restarted.ready;
  const after={...db.counts};await restarted.sync([changed]);assert.deepEqual(db.counts,after);
  db.sqlite.exec('UPDATE edge_issuers SET active=0');
  assert.equal((await restarted.sync([changed],true)).changed,1);
  // Transaction rollback: a failed upsert cannot leave the universe deactivated.
  db.sqlite.exec("CREATE TRIGGER reject_bad BEFORE INSERT ON edge_issuers WHEN NEW.ticker='BAD' BEGIN SELECT RAISE(ABORT,'fixture'); END");
  await assert.rejects(()=>edge.sync([{...issuer,cik:'456',ticker:'BAD'}]));
  assert.equal(db.sqlite.prepare('SELECT active FROM edge_issuers WHERE cik=?').get('123').active,1);
  await edge.sync([issuer]);
  globalThis.fetch=async()=>new Response(rss(65),{headers:{ETag:'"v1"'}});
  assert.equal(parseWire(rss(65)).length,65);
  await edge.pollWire('businesswire','https://feed.example');
  assert.equal(db.sqlite.prepare('SELECT COUNT(*) n FROM edge_candidates').get().n,65);
  assert.equal(db.sqlite.prepare('SELECT COUNT(*) n FROM edge_events').get().n,65);
  const warm={...db.counts};await edge.pollWire('businesswire','https://feed.example');assert.deepEqual(db.counts,warm);
  const restart=new HotEdge(s,{ALERTS_DB:db});await restart.ready;await restart.issuers();
  const restartWarm={...db.counts};await restart.pollWire('businesswire','https://feed.example');assert.deepEqual(db.counts,restartWarm);
  globalThis.fetch=async(_url,{headers})=>{assert.equal(headers['If-None-Match'],'"v1"');return new Response(null,{status:304});};
  await restart.pollWire('businesswire','https://feed.example');assert.deepEqual(db.counts,restartWarm);
  globalThis.fetch=async()=>new Response(rss(65,0,'updated evidence'));
  await edge.pollWire('businesswire','https://feed.example');assert.ok(db.counts.writes>warm.writes);
  await edge.sync([changed]);
  globalThis.fetch=async(_url,{headers})=>{assert.equal(headers['If-None-Match'],undefined);return new Response(rss(65));};
  await edge.pollWire('businesswire','https://feed.example');
  // A publisher still naming the old ticker must become unresolved, not inherit it.
  assert.equal(db.sqlite.prepare('SELECT ticker FROM edge_candidates LIMIT 1').get().ticker,null);
  globalThis.fetch=async()=>new Response(rss(65,1000));await edge.pollWire('businesswire','https://feed.example');
  assert.ok(edge.sourceState.businesswire.gap);await edge.checkpoint();
  const gapRestart=new HotEdge(s,{ALERTS_DB:db});await gapRestart.ready;assert.ok(gapRestart.sourceState.businesswire.gap);
  const publicHealth=await (await gapRestart.fetch(new Request('https://edge.internal/health'))).json();
  assert.equal(publicHealth.source_schedule.businesswire.window,undefined);
  // A failed candidate insert never becomes a dedup success.
  const failedDb=database(),failed=new HotEdge(state(),{ALERTS_DB:failedDb});
  failedDb.sqlite.exec("CREATE TRIGGER fail_insert BEFORE INSERT ON edge_candidates BEGIN SELECT RAISE(ABORT,'fixture'); END");
  globalThis.fetch=async()=>new Response(rss(1));
  await assert.rejects(()=>failed.pollWire('businesswire','https://feed.example'));
  assert.equal(failed.committed.size,0);assert.equal(failed.candidateCache.size,0);
  failedDb.sqlite.exec('DROP TRIGGER fail_insert');
  failedDb.sqlite.exec("CREATE TRIGGER fail_event BEFORE INSERT ON edge_events BEGIN SELECT RAISE(ABORT,'fixture'); END");
  await assert.rejects(()=>failed.pollWire('businesswire','https://feed.example'));
  assert.equal(failedDb.sqlite.prepare('SELECT COUNT(*) n FROM edge_candidates').get().n,0);
  assert.equal(failed.committed.size,0);
  failedDb.sqlite.exec('DROP TRIGGER fail_event');await failed.pollWire('businesswire','https://feed.example');assert.equal(failed.committed.size,1);
  const originalId=failedDb.sqlite.prepare('SELECT candidate_id FROM edge_candidates').get().candidate_id;
  // Attaching a source GUID to a legacy row preserves its completion receipt.
  failedDb.sqlite.exec("UPDATE edge_candidates SET source_item_id=NULL,completed_at='2026-10-09',github_run_id='42',lifecycle='PUBLISHED'");
  failed.committed.clear();await failed.pollWire('businesswire','https://feed.example');
  assert.equal(failedDb.sqlite.prepare('SELECT completed_at FROM edge_candidates').get().completed_at,'2026-10-09');
  // Stable GUID identity survives URL changes after dedup eviction.
  failed.committed.clear();globalThis.fetch=async()=>new Response(rss(1).replace('/news/0','/news/updated-url'));
  await failed.pollWire('businesswire','https://feed.example');
  assert.equal(failedDb.sqlite.prepare('SELECT COUNT(*) n FROM edge_events').get().n,1);
  assert.equal(failedDb.sqlite.prepare('SELECT candidate_id FROM edge_candidates').get().candidate_id,originalId);
  // Downgrades retain the event but remove it from delivery eligibility.
  globalThis.fetch=async()=>new Response(rss(1).replace('/news/0','/news/updated-url').replace('Phase 2 results','appoints CFO').replace('met its primary endpoint.','routine management announcement.'));
  await failed.pollWire('businesswire','https://feed.example');
  assert.equal(failedDb.sqlite.prepare('SELECT material FROM edge_events').get().material,0);
  // SEC bursts >100 paginate and keep all matching issuers, even on first start.
  const secDb=database(),sec=new HotEdge(state(),{ALERTS_DB:secDb,SEC_USER_AGENT:'test'});
  const pages=[];globalThis.fetch=async url=>{const start=Number(new URL(url).searchParams.get('start'));pages.push(start);return new Response(start===0?atom(100):atom(45,100));};
  await sec.pollSec('8-K');assert.deepEqual(pages,[0,100]);assert.equal(secDb.sqlite.prepare('SELECT COUNT(*) n FROM edge_events').get().n,145);
  const secWarm={...secDb.counts};await sec.pollSec('8-K');assert.deepEqual(secDb.counts,secWarm);
  globalThis.fetch=async url=>new Response(Number(new URL(url).searchParams.get('start'))===0?atom(100,1000):atom(1,1100));
  await sec.pollSec('8-K');assert.ok(sec.sourceState['sec_8-K'].gap);
  globalThis.fetch=async()=>Response.json({filings:{recent:{accessionNumber:['0000000123-26-009999'],form:['8-K'],filingDate:['2026-10-09'],acceptanceDateTime:['2026-10-09T08:00:00Z']},files:[]}});
  await sec.backfillSec();assert.equal(sec.sourceState['sec_8-K'].gap,null);
  assert.equal(secDb.sqlite.prepare("SELECT COUNT(*) n FROM edge_events WHERE accession='0000000123-26-009999'").get().n,1);
  const burstDb=database(),burst=new HotEdge(state(),{ALERTS_DB:burstDb,SEC_USER_AGENT:'test',EDGE_ENRICHMENT_ENABLED:'0'});
  globalThis.fetch=async url=>{
    const u=new URL(url);
    if(u.pathname.includes('browse-edgar'))return new Response(u.searchParams.get('type')==='6-K'?'<feed></feed>':u.searchParams.get('start')==='0'?atom(100):atom(45,100));
    if(u.hostname==='www.sec.gov')return new Response(u.pathname.endsWith('filing-index.htm')?'<tr><td>EX-99.1</td><td><a href="ex99.htm">exhibit</a></td></tr>':'Novel Bio met its primary endpoint');
    return new Response(rss(65));
  };
  await burst.alarm();assert.ok(burstDb.counts.queries<50,`cold burst used ${burstDb.counts.queries} D1 queries`);
  assert.equal(burstDb.sqlite.prepare("SELECT COUNT(*) n FROM edge_events WHERE source='sec' AND analysis_status='complete'").get().n,12);
  // Single execution under concurrent alarms, and persistent source schedules.
  const clock=state(),sched=new HotEdge(clock,{ALERTS_DB:database()});let polls=0;
  sched.pollSec=async()=>{polls++;};sched.pollWire=async()=>{polls++;};sched.analyzePending=async()=>({failed:0});sched.flushPending=async()=>{};
  await Promise.all([sched.alarm(),sched.alarm(),sched.alarm()]);assert.equal(polls,4);
  await sched.alarm();assert.equal(polls,4);
  const resume=new HotEdge(clock,{ALERTS_DB:database()});resume.pollSec=sched.pollSec;resume.pollWire=sched.pollWire;resume.analyzePending=sched.analyzePending;resume.flushPending=sched.flushPending;
  await resume.alarm();assert.equal(polls,4);
  const healthBefore={...db.counts};await edge.summaries();const healthWarm={...db.counts};await edge.summaries();assert.deepEqual(db.counts,healthWarm);assert.ok(healthWarm.queries>healthBefore.queries);
  assert.equal(budgetMode('NORMAL',.41),'ECO');assert.equal(budgetMode('ECO',.35),'ECO');assert.equal(budgetMode('ECO',.29),'NORMAL');
  assert.equal(budgetMode('ECO',.61),'PROTECTION');assert.equal(budgetMode('PROTECTION',.55),'PROTECTION');assert.equal(budgetMode('PROTECTION',.49),'ECO');
  const usage=new Usage();usage.record('candidate',{meta:{rows_read:100000,rows_written:10}});assert.equal(usage.report({}).mode,'PROTECTION');
  assert.equal(usage.report({}).account_usage_available,false);
  const bad=instrumentDatabase({prepare:()=>({run:async()=>({success:false})}),batch:async()=>[{success:false}]},new Usage());
  await assert.rejects(()=>bad.prepare('INSERT INTO edge_candidates').run());
  await assert.rejects(()=>bad.batch([bad.prepare('INSERT INTO edge_candidates')]));
  // Public alert caching invalidates on publication and cannot be repopulated
  // by an older concurrent GET that finishes after the publish.
  db.sqlite.exec(readFileSync(new URL('./migrations/0001_alert_feed.sql',import.meta.url),'utf8'));
  const feed={schema:1,sequence:1,generated_at:new Date().toISOString(),revision:'a'.repeat(64),alerts:[]};
  db.sqlite.prepare('INSERT INTO alert_feed VALUES(1,?,?,?,?,?)').run(1,feed.generated_at,feed.revision,JSON.stringify(feed),feed.generated_at);
  edge.env.ALERT_FEED_TOKEN='s';
  const getAlert=()=>edge.fetch(new Request('https://edge/alerts'));
  const publish=seq=>edge.fetch(new Request('https://edge/alerts',{method:'POST',headers:{Authorization:'Bearer s','Content-Type':'application/json'},body:JSON.stringify({...feed,sequence:seq})}));
  await getAlert();const alertWarm={...db.counts};await getAlert();assert.deepEqual(db.counts,alertWarm);
  assert.equal((await publish(2)).status,200);assert.equal((await (await getAlert()).json()).sequence,2);
  edge.alertCache=null;
  const prepare=db.prepare;let release,blocked=false;
  db.prepare=sql=>{
    const stmt=prepare(sql);
    if(sql.startsWith('SELECT payload,received_at')&&!blocked){blocked=true;const all=stmt.all;stmt.all=async()=>{const result=await all();await new Promise(resolve=>{release=resolve;});return result;};}
    return stmt;
  };
  const oldGet=getAlert();while(!release)await new Promise(resolve=>setTimeout(resolve,0));
  await publish(3);release();await oldGet;
  assert.equal((await (await getAlert()).json()).sequence,3);db.prepare=prepare;
  // Preserve old event records and reject aliases of an already supplied CIK.
  const env={EDGE_SYNC_TOKEN:'s',HOT_EDGE:{idFromName:()=>0,get:()=>edge}};
  const request=new Request('https://edge/edge/sync',{method:'POST',headers:{Authorization:'Bearer s'},body:JSON.stringify({issuers:[issuer,{...issuer,cik:'000123'}]})});
  assert.equal((await syncIssuers(request,env)).status,400);
  assert.ok(db.sqlite.prepare('SELECT COUNT(*) n FROM edge_events').get().n>=65);
  const bound=new HotEdge(state(),{ALERTS_DB:database()});for(let i=0;i<13000;i++)bound.remember(`EDGE-${i}`,`hash-${i}`);assert.equal(bound.committed.size,12000);await bound.checkpoint();
  bound.pruneCache(Date.now()+49*3600000);assert.equal(bound.committed.size,0);
  // The public health route has no direct D1 reads.
  const routed={...env,HOT_EDGE:{idFromName:()=>0,get:()=>({fetch:req=>edge.fetch(typeof req==='string'?new Request(req):req)})}};
  assert.equal((await worker.fetch(new Request('https://edge/edge/health'),routed)).status,200);
  console.log('D1 optimization: cache, differential sync, restart, failure, burst, continuity, concurrency, health and budget regressions passed');
} finally {globalThis.fetch=originalFetch;Date.now=originalNow;}
