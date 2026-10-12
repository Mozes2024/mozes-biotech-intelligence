import test from 'node:test';
import assert from 'node:assert/strict';
import {publicFeed} from './src/alert-feed.js';
const alert={change_id:'CHG-123',priority:'P1',polarity:'negative',ticker:'ACME',
 event_family:'clinical_outcome',event_outcome:'primary_endpoint_failed',
 evidence_basis:'source_text',semantic_version:'alert-evidence-v1',uncertainty_status:'none',
 issuer_verified:true,confirmed_outcome:true,needs_review:false,popup_eligible:true};
const feed=rows=>({schema:1,sequence:1,revision:'a'.repeat(64),generated_at:'2026-10-12T12:00:00Z',alerts:rows});
test('serialized public feed preserves the explicit interruptive contract and removes private fields',()=>{
 const result=JSON.parse(JSON.stringify(publicFeed(feed([{...alert,secret:'private'}])))).alerts[0];
 for(const key of ['event_family','event_outcome','evidence_basis','semantic_version','uncertainty_status',
  'issuer_verified','confirmed_outcome','needs_review','popup_eligible'])assert.equal(result[key],alert[key]);
 assert.ok(!Object.hasOwn(result,'secret'));
});
for(const field of ['event_family','event_outcome','evidence_basis','semantic_version','issuer_verified','confirmed_outcome','needs_review'])
 test(`missing ${field} fails closed for popups without dropping the list row`,()=>{
  const value={...alert};delete value[field];const result=publicFeed(feed([value])).alerts;
  assert.equal(result.length,1);assert.equal(result[0].change_id,alert.change_id);assert.equal(result[0].popup_eligible,false);
 });
test('legacy alerts retain identity, polarity, priority and list visibility',()=>{
 const legacy={change_id:'CHG-123',priority:'P1',polarity:'positive',headline:'Historical ATOS'};
 const result=publicFeed(feed([legacy])).alerts[0];
 for(const key of Object.keys(legacy))assert.equal(result[key],legacy[key]);assert.equal(result.popup_eligible,false);
});
test('complete source clauses, offsets and hashes survive serialization without truncation',()=>{
 const quote='No product has been approved by FDA. If approval is granted, a future payment may be due.';
 const evidence={id:'source-absence',quote,url:'https://www.sec.gov/Archives/document.htm',topic:'regulatory',source_hash:'b'.repeat(64),start:10,end:10+quote.length};
 const explanation={basis:'source_text',method:'source-rules-v2',status:'complete',missing_he:[],evidence:[evidence]};
 const result=publicFeed(feed([{...alert,explanation}])).alerts[0].explanation.evidence[0];assert.deepEqual(result,evidence);
});
test('oversized excerpts are omitted with a limitation instead of cutting away a condition',()=>{
 const quote='FDA approved the product '+ 'under the terms '.repeat(45)+'only if a future milestone occurs.';
 const explanation={basis:'source_text',method:'source-rules-v2',status:'complete',evidence:[{quote,url:'https://www.sec.gov/document.htm'}]};
 const result=publicFeed(feed([{...alert,explanation}])).alerts[0].explanation;
 assert.deepEqual(result.evidence,[]);assert.equal(result.missing_he.length,1);
});
