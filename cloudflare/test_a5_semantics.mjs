import {readFileSync} from 'node:fs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {classifyEvidence} from './src/alert-semantics.js';
import {HotEdge,fastOutcome} from './src/hot-edge.js';
import {persistWireBatch} from './src/edge-runtime.js';
import {DatabaseSync} from 'node:sqlite';
const corpus=JSON.parse(readFileSync(new URL('../tests/fixtures/a5/alert_evidence_corpus.json',import.meta.url)));
for(const c of corpus.cases)test(c.id,()=>{
  const actual=classifyEvidence(c.text,c.basis,'2026-10-12');
  assert.deepEqual(Object.fromEntries(Object.keys(c.expected).map(k=>[k,actual[k]])),c.expected);
  assert.deepEqual(fastOutcome(c.text,c.basis,'2026-10-12'),actual,'active Stage-0 wrapper uses the contract');
});

test('temporal scope is anchored to source publication, including RFC feed dates',()=>{
 for(const reference of ['2026-06-20T12:00:00Z','Sat, 20 Jun 2026 12:00:00 GMT','2026-07-01T00:30:00+03:00'])assert.equal(classifyEvidence('FDA approved DrugX in June 2026.','source_text',reference).confirmed_outcome,true);
 assert.equal(classifyEvidence('FDA approved DrugX in June 2026.','source_text','2026-10-12').confirmed_outcome,false);
});

test('procedural authorizations cannot manufacture a marketing approval',()=>{
 for(const text of ['FDA approved the clinical trial protocol.','FDA approved the manufacturing facility.','FDA approved the application for review.']){
  const result=fastOutcome(text,'source_text');assert.equal(result.event_family,'regulatory_milestone');assert.equal(result.polarity,'unknown');assert.equal(result.confirmed_outcome,false);
 }
});

test('line wrapping, conditional semicolons and explicit-absence conflicts fail closed',()=>{
 for(const text of ['No product developed by the company\nhas been approved by FDA.','<p>No product developed by the company\nhas been approved by FDA.</p>',
  'If the application succeeds; FDA approved the NDA.','FDA approved the NDA. No product has been approved by FDA.',
  '<p>&#78;o product has been approved by FDA.</p>','<p>&#x49;f FDA approved DrugX, payments would be due.</p>'])assert.equal(fastOutcome(text,'source_text').confirmed_outcome,false);
});

test('an abbreviation cannot turn a cut conditional sentence into an approval',()=>{
 const result=fastOutcome('FDA approved DrugX for U.S. patients '+'under the defined treatment conditions '.repeat(500)+' if the application succeeds.','source_text');
 assert.equal(result.confirmed_outcome,false);assert.equal(result.needs_review,true);assert.equal(result.uncertainty_status,'truncated_source');
});

test('Stage-0 interrupt requires complete source evidence; legacy and conditional events still enrich',async()=>{
 let pushes=0,writes=0;
 const db={prepare:()=>({bind(){return this},async run(){writes++;return {meta:{changes:1}}}})};
 const edge=new HotEdge({storage:{}},{ALERTS_DB:db,NTFY_URL:'https://offline.invalid/push'});
 const old=globalThis.fetch;globalThis.fetch=async()=>{pushes++;return new Response('ok')};
 try{
  const base={event_id:'EDGE-'+'a'.repeat(24),source:'sec',ticker:'ZZZZ',cik:'123',material:1,actionable:1,headline:'FDA event',source_url:'https://www.sec.gov/source.htm'};
  for(const raw of ['FDA approval is expected next quarter.','No product has been approved by FDA.','Under the CVR Agreement, payments occur if FDA approval is granted.']){
   await edge.deliver({...base,classification_json:JSON.stringify(classifyEvidence(raw,'source_text'))},undefined,false);
  }
  await edge.deliver(base,undefined,false);assert.equal(pushes,0);assert.equal(writes,0);
  await edge.deliver({...base,classification_json:JSON.stringify(classifyEvidence('FDA issued a Complete Response Letter.','source_text'))},undefined,false);
  assert.equal(pushes,1);assert.equal(writes,1);
 }finally{globalThis.fetch=old;}
});

test('same-source completed candidate and receipt survive a semantic-only policy change',async()=>{
 const sqlite=new DatabaseSync(':memory:');
 for(const name of ['0002_hot_edge.sql','0003_hot_edge_reliability.sql','0004_clinical_candidates.sql','0005_d1_optimization.sql'])sqlite.exec(readFileSync(new URL('./migrations/'+name,import.meta.url),'utf8'));
 const db={prepare(sql){let args=[];return {bind(...v){args=v;return this},async run(){return sqlite.prepare(sql).run(...args)}}},async batch(statements){for(const s of statements)await s.run()}};
 const row={event_id:'EDGE-'+'c'.repeat(24),source:'businesswire',source_url:'https://www.businesswire.com/test',headline:'Old filing',summary:'Unchanged source',published_at:'2026-10-08',first_seen_at:'2026-10-08',ticker:'ZZZZ',cik:'123',ticker_hint:null,
  classification_json:JSON.stringify({material:true,polarity:'positive'}),lifecycle:'CLASSIFIED',suppression_reason:null,history_json:'[]',content_hash:'d'.repeat(64),source_item_id:'guid',polarity:'positive',material:1,relevant:1,actionable:1,catalyst:0};
 await persistWireBatch(db,[row]);sqlite.exec("UPDATE edge_candidates SET completed_at='2026-10-09',github_run_id='42',lifecycle='PUBLISHED';UPDATE edge_events SET enrichment_ack_at='2026-10-09'");
 const before=sqlite.prepare('SELECT * FROM edge_candidates').get(),event=sqlite.prepare('SELECT * FROM edge_events').get();
 await persistWireBatch(db,[{...row,classification_json:JSON.stringify(classifyEvidence('If FDA approval is granted, payments occur.','source_text')),polarity:'unknown'}]);
 assert.deepEqual(sqlite.prepare('SELECT * FROM edge_candidates').get(),before);
 assert.deepEqual(sqlite.prepare('SELECT * FROM edge_events').get(),event);sqlite.close();
});
