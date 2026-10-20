import assert from 'node:assert/strict';
import {database,state} from './test-db.mjs';
import {HotEdge} from './src/hot-edge.js';
import worker from './src/index.js';

const db=database(),id='EDGE-fe3b6ebc9a7186e22f7d78b8';
const base='https://www.sec.gov/Archives/edgar/data/1345099/000134509926000087/';
const index=base+'0001345099-26-000087-index.htm';
db.sqlite.prepare(`INSERT INTO edge_events(event_id,source,source_url,filing_index_url,ticker,cik,accession,
  first_seen_at,polarity,material,analysis_status,analysis_attempts)
  VALUES(?,'sec',?,?,'MESO','1345099','0001345099-26-000087','2026-10-13T10:29:04.356Z','unknown',0,'failed',167)`)
  .run(id,index,index);
let calls=0;const original=globalThis.fetch;
globalThis.fetch=async url=>{
  calls++;
  assert.ok(String(url).startsWith(base));
  return new Response(String(url)===index?'<tr><td>EX-99.1</td><td><a href="exhibit991annualreportag.htm">Exhibit 99.1</a></td></tr>':'x'.repeat(1092359));
};
try{
  const edge=new HotEdge(state(),{ALERTS_DB:db,SEC_USER_AGENT:'offline test'});
  const result=await edge.analyzePending('2026-10-20T13:00:00Z');
  assert.equal(result.processed,0);assert.equal(result.failed,1);
  const row=db.sqlite.prepare('SELECT * FROM edge_events WHERE event_id=?').get(id);
  assert.equal(row.analysis_status,'blocked');
  assert.equal(row.analysis_attempts,168,'attempt history is retained');
  assert.equal(row.analysis_retry_at,null);
  assert.match(row.last_analysis_error,/size limit/);
  assert.equal(row.enrichment_ack_at,null);
  assert.equal(row.analysis_completed_at,null);
  await edge.analyzePending('2026-10-21T13:00:00Z');
  assert.equal(calls,2,'a fixed size limit cannot be healed by hourly retries');
  const summaries=await edge.summaries();
  assert.equal(summaries.backlog.analysis_pending,1);
  assert.equal(summaries.backlog.analysis_blocked,1);
  const env={HOT_EDGE:{idFromName:()=>'',get:()=>({fetch:async url=>Response.json(String(url).endsWith('/summaries')?summaries:{sources:{sec:{status:'OK'}}})})}};
  const health=await (await worker.fetch(new Request('https://edge.example/edge/health'),env)).json();
  assert.equal(health.status,'PARTIAL','healthy transport cannot hide a blocked analysis');
}finally{globalThis.fetch=original;}
console.log('Oversized SEC sources remain blocked, visible and unacknowledged without endless retries');
