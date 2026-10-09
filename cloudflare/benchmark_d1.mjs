// Local workerd D1 metadata, not production/account analytics. Repeated baseline
// ticks are weighted only after verifying they have no new input or delivery.
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import {execFileSync} from 'node:child_process';
import {readFileSync,writeFileSync,unlinkSync,existsSync} from 'node:fs';
import {HotEdge} from './src/hot-edge.js';
import {candidateStats} from './src/candidate-contract.js';
import {edgeMetrics} from './src/edge-metrics.js';
import {state} from './test-db.mjs';

const baselineRef=process.env.BENCHMARK_BASE_REF || '116d78d9bce29ef94836a47753c656b59c462871';
const baselinePath=new URL('./src/.benchmark-baseline.mjs',import.meta.url);
if(existsSync(baselinePath))throw Error('baseline scratch file already exists');
writeFileSync(baselinePath,execFileSync('git',['show',`${baselineRef}:cloudflare/src/hot-edge.js`],{encoding:'utf8'}));
const OldHotEdge=(await import(baselinePath.href)).HotEdge;
const OriginalDate=Date,originalFetch=fetch;
let clock=Date.parse('2026-10-09T00:00:00Z');
class ClockDate extends OriginalDate {constructor(...args){super(...(args.length?args:[clock]));}static now(){return clock;}}
const mf=new Miniflare(convertV4MiniflareOptions({modules:true,script:'export default {fetch(){return new Response("benchmark")}}',compatibilityDate:'2026-08-01',d1Databases:{BEFORE:'before',AFTER:'after',PLAN:'plan'}}));
const issuers=Array.from({length:200},(_,i)=>({cik:String(123+i),ticker:i?'B'+i:'ZZZZ',company:i?'Issuer '+i:'Novel Bio',confidence:.99,source:'benchmark'}));
function meter(db){
 const totals={queries:0,rows_read:0,rows_written:0};
 const record=r=>{if(!Number.isFinite(r.meta?.rows_read)||!Number.isFinite(r.meta?.rows_written))throw Error('D1 metadata missing');totals.queries++;totals.rows_read+=r.meta.rows_read;totals.rows_written+=r.meta.rows_written;};
 const wrap=s=>({bind(...a){return wrap(s.bind(...a));},async all(){const r=await s.all();record(r);return r;},async first(){const r=await this.all();return r.results[0]||null;},async run(){const r=await s.run();record(r);return r;},original:s});
 return {totals,prepare:q=>wrap(db.prepare(q)),async batch(ss){const rr=await db.batch(ss.map(s=>s.original));rr.forEach(record);return rr;}};
}
const difference=(a,b)=>Object.fromEntries(Object.keys(a).map(k=>[k,a[k]-b[k]]));
const add=(a,b,weight=1)=>Object.keys(a).forEach(k=>a[k]+=b[k]*weight);
async function migrate(db,optimization=false){
 for(const name of ['0002_hot_edge.sql','0003_hot_edge_reliability.sql','0004_clinical_candidates.sql',...(optimization?['0005_d1_optimization.sql']:[])]) {
  const sql=readFileSync(new URL('./migrations/'+name,import.meta.url),'utf8');
  for(const statement of sql.split(';').filter(s=>s.trim()))await db.prepare(statement).run();
 }
 for(const row of issuers)await db.prepare('INSERT INTO edge_issuers VALUES(?,?,?,?,?,1,?)').bind(row.cik,row.ticker,row.company,row.confidence,row.source,new Date(clock).toISOString()).run();
}
function feed(source,hour){return '<rss><channel>'+Array.from({length:30},(_,i)=>{
 const id=i?`${source}-stable-${i}`:`${source}-material-${hour}`;
 return `<item><guid>${id}</guid><title>${i?'Industry conference calendar':'Novel Bio met its primary endpoint'}</title><link>https://www.${source}.com/news/${id}</link><description>${i?'Routine industry meeting':'Novel Bio (NASDAQ: ZZZZ) Phase 2 topline results'}</description><pubDate>${new Date(Date.parse('2026-10-09T00:00:00Z')+hour*3600000).toUTCString()}</pubDate></item>`;
}).join('')+'</channel></rss>';}
function secFeed(form,hour){return '<feed>'+Array.from({length:hour+1},(_,n)=>{
 const h=hour-n,accession=`0000000123-26-${String(h*2+(form==='8-K'?1:2)).padStart(6,'0')}`;
 const url=`https://www.sec.gov/Archives/edgar/data/123/${accession.replaceAll('-','')}/filing-index.htm`;
 return `<entry><title>${form} - Novel Bio (123) (Filer)</title><link href="${url}"/><id>accession-number=${accession}</id><updated>${new Date(Date.parse('2026-10-09T00:00:00Z')+h*3600000).toISOString()}</updated></entry>`;
}).join('')+'</feed>';}
try {
 globalThis.Date=ClockDate;
 const beforeDb=await mf.getD1Database('BEFORE'),afterDb=await mf.getD1Database('AFTER');
 await migrate(beforeDb);await migrate(afterDb,true);
 const before=meter(beforeDb),after=meter(afterDb),old=new OldHotEdge(state(),{ALERTS_DB:before,SEC_USER_AGENT:'offline fixture',EDGE_ENRICHMENT_ENABLED:'0'});
 const edge=new HotEdge(state(),{ALERTS_DB:after,SEC_USER_AGENT:'offline fixture',EDGE_ENRICHMENT_ENABLED:'0'});
 const weighted={queries:0,rows_read:0,rows_written:0};
 const snapshots=96,healthRequests=1440;
 let hour=0;
 globalThis.fetch=async url=>{
  const u=new URL(url);
  if(u.pathname.includes('browse-edgar'))return new Response(secFeed(u.searchParams.get('type'),hour));
  if(u.hostname==='www.sec.gov')return new Response(u.pathname.endsWith('filing-index.htm')?'<tr><td>EX-99.1</td><td><a href="ex99.htm">Exhibit</a></td></tr>':'Novel Bio Phase 2 topline results: met its primary endpoint');
  return new Response(feed(String(url).includes('globenewswire')?'globenewswire':'businesswire',hour));
 };
 async function oldTick(){await old.pollSec('8-K');await old.pollSec('6-K');await old.pollWire('businesswire','https://feed.businesswire.com/rss');await old.pollWire('globenewswire','https://rss.globenewswire.com/feed');await old.analyzePending();await old.flushPending();}
 for(hour=0;hour<24;hour++) {
  clock=Date.parse('2026-10-09T00:00:00Z')+hour*3600000;
  let start={...before.totals};await oldTick();add(weighted,difference(before.totals,start));
  start={...before.totals};await oldTick();add(weighted,difference(before.totals,start),239);
  const summaryStart={...before.totals};
  await before.prepare("SELECT COUNT(*) AS events,SUM(CASE WHEN analysis_status<>'complete' THEN 1 ELSE 0 END) AS analysis_pending,SUM(CASE WHEN material=1 AND enrichment_ack_at IS NULL THEN 1 ELSE 0 END) AS enrichment_pending FROM edge_events").first();
  await candidateStats(before);await edgeMetrics(before);add(weighted,difference(before.totals,summaryStart),60);
  for(let minute=0;minute<60;minute++) {
   clock=Date.parse('2026-10-09T00:00:00Z')+hour*3600000+minute*60000;
   if(minute%15===0)await edge.sync(issuers);
   await edge.alarm();await edge.summaries();
  }
 }
 // Old sync deactivates every issuer and upserts the entire snapshot each run.
 const syncStart={...before.totals};
 await before.batch([before.prepare('UPDATE edge_issuers SET active=0'),...issuers.map(r=>before.prepare("INSERT INTO edge_issuers VALUES(?,?,?,?,?,1,?) ON CONFLICT(cik) DO UPDATE SET ticker=excluded.ticker,company=excluded.company,confidence=excluded.confidence,source=excluded.source,active=1,updated_at=excluded.updated_at").bind(r.cik,r.ticker,r.company,r.confidence,r.source,new Date(clock).toISOString()))]);
 add(weighted,difference(before.totals,syncStart),snapshots);
 const report={schema:1,baseline_ref:execFileSync('git',['rev-parse',baselineRef],{encoding:'utf8'}).trim(),method:'24-hour synthetic workload; workerd local D1 meta; weighted unchanged baseline ticks, optimized 1440 actual alarm cycles',
  fixture:{issuers:200,rss_items_per_feed:30,new_material_wire_items_per_hour:2,sec_new_filings:48,health_requests:healthRequests,issuer_syncs:snapshots,enrichment:'disabled; reliability verified separately'},
  before:weighted,after:after.totals,query_reduction_percent:100*(1-after.totals.queries/weighted.queries),reduction_vs_observed_329000_percent:100*(1-after.totals.queries/329000),
  daily_quota_percent:{before_read:weighted.rows_read/50000,after_read:after.totals.rows_read/50000,before_write:weighted.rows_written/1000,after_write:after.totals.rows_written/1000},
  detection_latency_seconds:{before:{scheduler:15,sec_max_poll_wait:15,rss_max_poll_wait:15},after:{scheduler:60,sec_max_poll_wait:180,rss_max_poll_wait:240},basis:'conservative interval plus scheduler bounds; excludes upstream lag, processing, network, backlog and protection/backoff'},
  limitations:['Not production/account totals','145-filing burst and archive recovery tested separately','External GitHub/alert delivery and account consumers excluded','Persistent hot instance; cold restart adds issuer query and can replay committed operations safely']};
 console.log(JSON.stringify(report,null,2));
 if(process.env.BENCHMARK_OUTPUT)writeFileSync(process.env.BENCHMARK_OUTPUT,JSON.stringify(report,null,2)+'\n');
} finally {globalThis.Date=OriginalDate;globalThis.fetch=originalFetch;await mf.dispose();unlinkSync(baselinePath);}
