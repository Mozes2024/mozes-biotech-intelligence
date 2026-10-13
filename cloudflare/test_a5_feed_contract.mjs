import test from 'node:test';
import assert from 'node:assert/strict';
import {publicFeed,clientFeed,handleAlertFeed} from './src/alert-feed.js';
import fs from 'node:fs';
import vm from 'node:vm';
const alert={change_id:'CHG-123',priority:'P1',polarity:'negative',ticker:'ACME',
 event_family:'clinical_outcome',event_outcome:'primary_endpoint_failed',
 evidence_basis:'source_text',semantic_version:'alert-evidence-v1',uncertainty_status:'none',
 issuer_verified:true,confirmed_outcome:true,needs_review:false,popup_eligible:true};
const feed=rows=>({schema:1,sequence:1,revision:'a'.repeat(64),generated_at:'2026-10-12T12:00:00Z',alerts:rows});

// Captured from main 6f939b8:web/alerts_ui.js; execute its real polling,
// popup and Notification paths, beginning with an initialized empty feed.
function oldBrowser(){
 const storage=new Map(),notifications=[],popup={hidden:true,innerHTML:'',querySelector:()=>({})};
 const c=vm.createContext({D:{alert_config:{}},apiMode:false,document:{hidden:false},navigator:{},
  window:{localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)}},
  Notification:class{static permission='granted';constructor(title,options){notifications.push(options.tag);}},
  $:()=>popup,esc:x=>x,alertWhyHe:a=>a.headline,render:()=>{},page:'dash'});
 c.window.Notification=c.Notification;
 vm.runInContext(fs.readFileSync(new URL('../tests/fixtures/a5_pre_a5_alerts_ui.js',import.meta.url),'utf8'),c);
 return {popup,notifications,async poll(value){c.fetch=async()=>({ok:true,json:async()=>JSON.parse(JSON.stringify(value))});await c.checkForAlerts();}};
}
test('reproduce: initialized actual pre-A5 script interrupts for a new serialized list-only row',async()=>{
 const browser=oldBrowser();await browser.poll(publicFeed(feed([])));
 await browser.poll(publicFeed({...feed([{...alert,popup_eligible:false}]),revision:'b'.repeat(64)}));
 assert.equal(browser.popup.hidden,false);assert.deepEqual(browser.notifications,['CHG-123']);
});
test('Worker GET separates legacy and evidence-aware clients without changing stored rows or receipts',async()=>{
 const snapshot=publicFeed({...feed([{...alert,popup_eligible:false}, {...alert,change_id:'CHG-456'}]),revision:'b'.repeat(64)});
 const serialized=JSON.stringify(snapshot),env={ALERTS_DB:{prepare:()=>({first:async()=>({payload:serialized,received_at:'receipt'})})}};
 const legacy=await (await handleAlertFeed(new Request('https://clock.example/alerts'),env)).json();
 const aware=await (await handleAlertFeed(new Request('https://clock.example/alerts?client=alert-evidence-v1'),env)).json();
 assert.deepEqual(legacy.alerts.map(a=>a.change_id),['CHG-456']);assert.equal(aware.alerts.length,2);
 assert.equal(aware.received_at,'receipt');assert.equal(aware.revision,snapshot.revision);assert.equal(JSON.stringify(snapshot),serialized);
 const browser=oldBrowser();await browser.poll(clientFeed(publicFeed(feed([]))));
 await browser.poll(legacy);await browser.poll(legacy);
 assert.deepEqual(browser.notifications,['CHG-456']);assert.ok(!browser.popup.innerHTML.includes('CHG-123'));
});
test('missing source verification suppresses eligibility independently of displayed excerpt availability',()=>{
 for(const basis of ['source_text','feed_summary','structured_source']) {
  const value=publicFeed(feed([{...alert,evidence_basis:basis}]));assert.equal(value.alerts[0].popup_eligible,true);
 }
 assert.equal(publicFeed(feed([{...alert,evidence_basis:null}])).alerts[0].popup_eligible,false);
});
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
