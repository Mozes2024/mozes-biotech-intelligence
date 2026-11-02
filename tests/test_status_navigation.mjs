import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const app=readFileSync('web/app.js','utf8');
test('full status appears only inside system, not global page rendering',()=>{
 assert.match(app,/function system\(\)[\s\S]*?\+checkpointBanner\(\)\+`/);
 assert.match(app,/\$\('main'\)\.innerHTML=html;/);
 assert.doesNotMatch(app,/innerHTML=checkpointBanner\(\)\+html/);
 assert.match(app,/system:\['⚙','סטטוס מערכת'\]/);
 assert.match(app,/if\(el&&page==='system'\)el.outerHTML=checkpointBanner\(\)/);
});
for(const [h,label] of [[{},'id="systemStatusIndicator"'],[{failed_sources:27},'כיסוי חלקי'],[{stale:true,failed_sources:27},'עדכון מתעכב'],[{publication_delayed:true},'עדכון מתעכב'],[{sources_stale:true},'כיסוי חלקי'],[{ir_coverage:{configured_without_observation:['X']}},'כיסוי חלקי']]){
 test(`sidebar remains truthful: ${JSON.stringify(h)}`,()=>{
  const ctx=vm.createContext({D:{},MozesCheckpointHealth:()=>h});
  vm.runInContext(app.slice(app.indexOf('function systemStatusIndicator()'),app.indexOf('function renderMenu()')),ctx);
  assert.match(ctx.systemStatusIndicator(),new RegExp(label));
 });
}

test('healthy status has no warning badge',()=>{const ctx=vm.createContext({D:{},MozesCheckpointHealth:()=>({})});vm.runInContext(app.slice(app.indexOf('function systemStatusIndicator()'),app.indexOf('function renderMenu()')),ctx);assert.doesNotMatch(ctx.systemStatusIndicator(),/small|כיסוי חלקי|מתעכב/);});
