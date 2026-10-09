import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import {readFileSync,writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const mf=new Miniflare(convertV4MiniflareOptions({modules:true,script:'export default {fetch(){return new Response("profile")}}',compatibilityDate:'2026-08-01',d1Databases:{DB:'profile'}}));
try {
 const db=await mf.getD1Database('DB');
 for(const name of ['0002_hot_edge.sql','0003_hot_edge_reliability.sql','0004_clinical_candidates.sql'])
  for(const sql of readFileSync(new URL('./migrations/'+name,import.meta.url),'utf8').split(';').filter(s=>s.trim()))await db.prepare(sql).run();
 await db.prepare(`WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<10000)
  INSERT INTO edge_events(event_id,source,source_url,ticker,first_seen_at,polarity,material,analysis_status,enrichment_ack_at)
  SELECT 'EDGE-'||printf('%024d',i),'sec','https://www.sec.gov/Archives/edgar/data/123/filing','ZZZZ',CASE WHEN i>9900 THEN '2026-10-09T08:00:00Z' ELSE '2026-10-01T08:00:00Z' END,'unknown',0,CASE WHEN i>9997 THEN 'pending' ELSE 'complete' END,'2026-10-01' FROM n`).run();
 await db.prepare(`WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n WHERE i<10000)
  INSERT INTO edge_candidates(candidate_id,source,source_url,headline,summary,first_seen_at,classification_json,lifecycle,suppression_reason,history_json,content_hash,completed_at)
  SELECT 'EDGE-'||printf('%024d',i),'businesswire','https://www.businesswire.com/news','fixture','','2026-10-01','{}','SUPPRESSED',CASE WHEN i>9997 THEN 'issuer_unresolved' ELSE 'outside_scope' END,'[]','fixture',CASE WHEN i>9997 THEN NULL ELSE '2026-10-01' END FROM n`).run();
 const queries={
  candidate_existence:['SELECT * FROM edge_candidates WHERE candidate_id=?',['EDGE-'+'1'.padStart(24,'0')]],
  analysis_pending:["SELECT * FROM edge_events WHERE source='sec' AND analysis_status IN ('pending','failed') AND (analysis_retry_at IS NULL OR analysis_retry_at<=?) ORDER BY first_seen_at,event_id LIMIT 3",['2026-10-09']],
  candidate_pending:["SELECT * FROM edge_candidates WHERE suppression_reason IN ('issuer_unresolved','source_unavailable','classification_uncertain') AND completed_at IS NULL AND (verification_retry_at IS NULL OR verification_retry_at<=?) ORDER BY first_seen_at LIMIT 20",['2026-10-09']],
  event_metrics:['SELECT source,accepted_at,published_at,first_seen_at,stage0_sent_at,enrichment_dispatch_at,enrichment_ack_at FROM edge_events WHERE first_seen_at>=? ORDER BY first_seen_at DESC LIMIT 2001',['2026-10-09']],
  active_enrichment:["SELECT COUNT(*) AS n FROM edge_events WHERE material=1 AND enrichment_ack_at IS NULL AND enrichment_retry_at>?",['2026-10-09']],
  enrichment_pending:["SELECT * FROM edge_events WHERE material=1 AND analysis_status='complete' AND (enrichment_ack_at IS NULL OR (actionable=1 AND stage0_sent_at IS NULL)) AND (enrichment_ack_at IS NULL AND (enrichment_retry_at IS NULL OR enrichment_retry_at<=?)) ORDER BY first_seen_at,event_id LIMIT 3",['2026-10-09']],
  backlog:["SELECT COUNT(*) AS events,SUM(CASE WHEN analysis_status<>'complete' THEN 1 ELSE 0 END) AS analysis_pending,SUM(CASE WHEN material=1 AND enrichment_ack_at IS NULL THEN 1 ELSE 0 END) AS enrichment_pending FROM edge_events",[]],
  candidate_statistics:['SELECT COUNT(*) AS n FROM edge_candidates',[]],
 };
 const collect=async()=>{
  const out={};for(const [name,[sql,values]] of Object.entries(queries)){
   const result=await db.prepare(sql).bind(...values).all();const plan=await db.prepare('EXPLAIN QUERY PLAN '+sql).bind(...values).all();
   out[name]={rows_read:result.meta.rows_read,rows_written:result.meta.rows_written,plan:plan.results.map(r=>r.detail)};
  }return out;
 };
 const before=await collect();
 queries.analysis_pending=["SELECT * FROM edge_events INDEXED BY idx_edge_analysis_pending WHERE source='sec' AND enrichment_ack_at IS NULL AND analysis_status IN ('pending','failed') AND (analysis_retry_at IS NULL OR analysis_retry_at<=?) ORDER BY first_seen_at,event_id LIMIT 3",['2026-10-09']];
 queries.policy_scan=['SELECT event_id FROM edge_events WHERE event_id>? ORDER BY event_id LIMIT 100',['']];
 for(const sql of readFileSync(new URL('./migrations/0005_d1_optimization.sql',import.meta.url),'utf8').split(';').filter(s=>s.trim()))await db.prepare(sql).run();
 const after=await collect();
 assert.ok(after.analysis_pending.rows_read<20,'ACK predicate must not turn analysis into a history scan');
 assert.ok(after.policy_scan.rows_read<=101,'policy scan must use bounded primary-key pagination');
 const report={basis:'local workerd D1 metadata and EXPLAIN QUERY PLAN; 10000 historical events/candidates, 3 pending rows',before,after};
 console.log(JSON.stringify(report,null,2));if(process.env.PROFILE_OUTPUT)writeFileSync(process.env.PROFILE_OUTPUT,JSON.stringify(report,null,2)+'\n');
}finally{await mf.dispose();}
