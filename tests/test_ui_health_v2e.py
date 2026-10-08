import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_overlay_health_is_collapsed_at_bottom():
    script = r"""
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync('web/live_intelligence_ui.js', 'utf8');
function render(red, amber) {
  const context = {D: {live: [], changes: [], alerts: [], generated_at: '2026-10-02T00:00:00Z',
    edge_health: {status:'PARTIAL',sources:{globenewswire:{status:'FAILED',last_error:'HTTP 520',consecutive_errors:2}},
      metrics:{windows:{'1h':{by_source:{sec:{detection:{n:2,p50:20,p95:29}}},metrics:{}}}}},
    validation: {}, coverage: {}, health: {sec_monitoring_enabled: true,
      source_freshness: [{source:'wire_feed:test',status:'FAILED',consecutive_failures:2,
        last_checked_at:'2026-10-08T00:00:00Z'}],
      latest_monitor: {finished_at: new Date().toISOString()},
      primary_catalyst_monitoring: {healthy: !red.length, label_he: 'ניטור אירועי הליבה תקין'},
      severity: {red, amber, info: []}}}, HE: {type: {}},
    decisionStatus: () => ({rank: 0}), focusCard: () => '', market: () => '',
    esc: x => String(x ?? ''), pageTitle: (t,_,m) => t + '|' + m, changedCompact: () => '',
    alertsCompact: () => 'ALERTS', fmtLocal: x => 'LOCAL:' + x, changeRows: () => ''};
  vm.createContext(context); vm.runInContext(source, context);
  return context.dashboard();
}
const amber = render([], [{module: 'prices', status: 'INCOMPLETE'}]);
assert(amber.startsWith('מה מעניין עכשיו?') || amber.includes('מה מעניין עכשיו?'));
assert(amber.indexOf('ALERTS') < amber.indexOf('סטטוס מערכת'));
assert(amber.includes('<details') && amber.includes('סטטוס מערכת'));
assert(amber.includes('p50 N/A') && amber.includes('n=0'));
assert(amber.includes('זיהוי Edge — SEC: p50 20.0'));
assert(amber.includes('משלוח Stage‑0'));
assert(amber.includes('השלמת העשרה Stage‑1 / ACK'));
assert(amber.includes('globenewswire: FAILED') && amber.includes('HTTP 520'));
assert(amber.includes('wire_feed:test: FAILED') && amber.includes('כשלים רצופים 2'));
assert(amber.includes('prices: INCOMPLETE'));
assert(!amber.includes('אזהרת מקור'));
const red = render([{module: 'refresh', status: 'FAILED'}], []);
assert(red.includes('red-note'));
assert(red.includes('refresh: FAILED'));
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout


def test_static_site_refreshes_snapshot_without_reloading_page():
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('web/app.js','utf8');
const start=source.indexOf('function updateDataLabel()');
const end=source.indexOf('function dashboard()',start);
assert(start>=0 && end>start);
const label={textContent:''};let rendered=0, fetched=[];
const context={D:{generated_at:'old',build:{snapshot_revision:'old'},alerts:[]},apiMode:false,
  $:id=>id==='apiLabel'?label:null,render:()=>rendered++,toast:()=>{},
  preserveAlertFeed:()=>{},
  fetch:async(url,options)=>{fetched.push([url,options.cache]);return {ok:true,json:async()=>
    url==='updates.json'?{revision:'new'}:{generated_at:'new',build:{snapshot_revision:'new'},alerts:[],
    health:{latest_monitor:{finished_at:new Date().toISOString()}}}}}};
vm.createContext(context);vm.runInContext(source.slice(start,end),context);
context.checkForUpdates().then(()=>{
  assert.deepStrictEqual(fetched,[['updates.json','no-store'],['data.json','no-store']]);
  assert.strictEqual(rendered,1);
  assert.strictEqual(context.D.generated_at,'new');
  return context.checkForUpdates();
}).then(()=>{
  assert.strictEqual(fetched.length,3);
  assert.strictEqual(fetched[2][0],'updates.json');
  assert.strictEqual(rendered,1);
}).catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr + result.stdout


def test_biotechish_keeps_health_canada_approvals():
    from mozes.ir_registry import resolve_alias
    from mozes.primary_feeds import BIOTECHISH

    headline = "Lipocine Announces Health Canada Approval of TLANDO for Testosterone Replacement Therapy"
    assert BIOTECHISH.search(headline)
    assert resolve_alias(headline)["ticker"] == "LPCN"
