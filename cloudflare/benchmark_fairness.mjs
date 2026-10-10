// Actual local workerd D1 metadata; no production calls or schema changes.
import assert from 'node:assert/strict';
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import {execFileSync} from 'node:child_process';
import {readFileSync,writeFileSync,unlinkSync,existsSync} from 'node:fs';
import {HotEdge} from './src/hot-edge.js';
import {state} from './test-db.mjs';

const baseline='38faf534d13de2ed520ca9340bbae7f468cdf62e';
const path=new URL('./src/.fairness-baseline.mjs',import.meta.url);
assert.ok(!existsSync(path));
writeFileSync(path,execFileSync('git',['show',`${baseline}:cloudflare/src/hot-edge.js`],{encoding:'utf8'}));
const Before=(await import(path.href)).HotEdge;
const originalFetch=fetch;
const mf=new Miniflare(convertV4MiniflareOptions({modules:true,script:'export default {fetch(){return new Response("offline")}}',compatibilityDate:'2026-08-01',d1Databases:{BEFORE:'before',AFTER:'after'}}));
function meter(db){
 const totals={queries:0,rows_read:0,rows_written:0};
 const record=r=>{assert.ok(Number.isFinite(r.meta?.rows_read));totals.queries++;totals.rows_read+=r.meta.rows_read;totals.rows_written+=r.meta.rows_written;};
 const wrap=s=>({bind(...values){return wrap(s.bind(...values));},async all(){const r=await s.all();record(r);return r;},async first(){return (await this.all()).results[0]||null;},async run(){const r=await s.run();record(r);return r;}});
 return {prepare:q=>wrap(db.prepare(q)),totals};
}
async function migrate(db,optimized){
 for(const name of ['0002_hot_edge.sql','0003_hot_edge_reliability.sql','0004_clinical_candidates.sql',...(optimized?['0005_d1_optimization.sql']:[])])
  for(const sql of readFileSync(new URL('./migrations/'+name,import.meta.url),'utf8').split(';').filter(s=>s.trim()))await db.prepare(sql).run();
}
async function seed(db,pending,history){
 await db.prepare('DELETE FROM edge_events').run();
 // Most retained history is nonmaterial with no enrichment ACK, like production.
 await db.prepare(`WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<?)
 INSERT INTO edge_events(event_id,source,source_url,ticker,first_seen_at,polarity,material,analysis_status,enrichment_ack_at)
 SELECT 'EDGE-'||printf('%024x',i),'businesswire','https://www.businesswire.com/fixture','HIST','2026-10-01T00:00:00Z','unknown',0,'complete',CASE WHEN i%20=0 THEN '2026-10-01' ELSE NULL END FROM n`).bind(history).run();
 if(pending)await db.prepare(`WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<?)
 INSERT INTO edge_events(event_id,source,source_url,ticker,first_seen_at,polarity,material,analysis_status)
 SELECT 'EDGE-'||printf('%024x',10000+i),'businesswire','https://www.businesswire.com/fixture','PEND',strftime('%Y-%m-%dT%H:%M:%SZ','2026-10-09',i||' seconds'),'unknown',1,'complete' FROM n`).bind(pending).run();
}
const delta=(current,before)=>Object.fromEntries(Object.keys(current).map(k=>[k,current[k]-before[k]]));
const results=[];
try {
 globalThis.fetch=async url=>{assert.equal(new URL(url).hostname,'api.github.com');return new Response(null,{status:204});};
 const beforeDb=await mf.getD1Database('BEFORE'),afterDb=await mf.getD1Database('AFTER');
 for(const optimized of [false,true]){
  if(!optimized){await migrate(beforeDb,false);await migrate(afterDb,false);}
  else for(const db of [beforeDb,afterDb])for(const sql of readFileSync(new URL('./migrations/0005_d1_optimization.sql',import.meta.url),'utf8').split(';').filter(s=>s.trim()))await db.prepare(sql).run();
  for(const history of [21,10000])for(const pending of [0,6,300,3000]){
   for(const db of [beforeDb,afterDb])await seed(db,pending,history);
   const before=meter(beforeDb),after=meter(afterDb),env=db=>({ALERTS_DB:db,GITHUB_TOKEN:'offline',GITHUB_REPO:'fixture/repo'});
   const old=new Before(state(),env(before)),edge=new HotEdge(state(),env(after));
   // 120-second production schedule, 900-second ACK deadline, no source ACKs.
   for(let tick=0;tick<32;tick++){
    const now=new Date(Date.parse('2026-10-10T00:00:00Z')+tick*120000).toISOString();
    await old.flushPending(now);await edge.flushPending(now);
   }
   const factor=720/32;
   const dailyAdditionalReads=Math.ceil((after.totals.rows_read-before.totals.rows_read)*factor);
   const report={schema:optimized?'0001–0005 local only':'0001–0004 production-compatible',history,pending,ticks:32,before:{...before.totals},after:{...after.totals},daily_additional_reads:dailyAdditionalReads,daily_additional_writes:Math.ceil((after.totals.rows_written-before.totals.rows_written)*factor),daily_additional_free_read_budget_percent:dailyAdditionalReads/50000};
   assert.ok(dailyAdditionalReads<=50000,'reject >1% additional daily free read budget');
   assert.ok(after.totals.rows_read<=before.totals.rows_read*1.1+100,'reject significant scan amplification');
   if(pending){
    // Exercise a tail-empty wrap; it must not grow with completed history after 0005.
    edge.deliveryCursor={first_seen_at:'2026-10-11T00:00:00Z',event_id:'EDGE-'+'f'.repeat(24)};
    await afterDb.prepare('UPDATE edge_events SET enrichment_retry_at=NULL WHERE material=1').run();
    const start={...after.totals};await edge.flushPending('2026-10-11T00:00:00Z');
    report.wrap=delta(after.totals,start);
    if(optimized)assert.ok(report.wrap.rows_read<=pending+50,'wrap must scan pending work rather than history');
   }
   results.push(report);
  }
 }
 const report={baseline,basis:'local workerd D1 metadata; 32 actual 120-second cycles, scaled to 720/day; 21 or 10000 retained rows; blocked publishers represented by no ACK',limits:'not live account usage; other Worker queries and consumers excluded; cursor uses DO storage, not D1',results};
 console.log(JSON.stringify(report,null,2));
 if(process.env.FAIRNESS_BENCHMARK_OUTPUT)writeFileSync(process.env.FAIRNESS_BENCHMARK_OUTPUT,JSON.stringify(report,null,2)+'\n');
} finally {globalThis.fetch=originalFetch;await mf.dispose();unlinkSync(path);}
