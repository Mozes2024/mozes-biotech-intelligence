import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import test from 'node:test';

const context = vm.createContext({});
vm.runInContext(readFileSync('web/checkpoint_health.js', 'utf8'), context);
const now = Date.parse('2026-10-20T12:00:00Z');

test('fresh publication and checkpoint cannot hide a stale successful source scan', () => {
  const data = {checkpoint_at:'2026-10-20T11:59:00Z', generated_at:'2026-10-20T11:59:30Z',
    health:{source_freshness:[{source:'sec_latest:8-K',status:'FAILED',last_success_at:'2026-10-19T19:23:48Z'}]},
    edge_health:{backlog:{enrichment_pending:30,analysis_pending:1}}};
  const h = context.MozesCheckpointHealth(data, now);
  assert.equal(h.stale, false);
  assert.equal(h.sources_stale, true);
  assert.equal(h.failed_sources, 1);
  assert.equal(h.publication_stale, false);
  assert.equal(h.enrichment_pending, 30);
});

test('unknown and future source times fail closed; skipped IR coverage is explicit', () => {
  const h = context.MozesCheckpointHealth({checkpoint_at:'2026-10-20T11:59:00Z',
    health:{source_freshness:[{last_success_at:'2026-10-21T00:00:00Z'}]},
    watch_universe:[{ticker:'TYRA'},{ticker:'ZZZZ'}], official_feeds:[{ticker:'TYRA',status:'fetch_error'}]}, now);
  assert.equal(h.source_scan_at, null);
  assert.equal(h.sources_stale, true);
  assert.equal(h.publication_stale, true);
  assert.equal(h.unscanned_ir_issuers, 1);
});

test('deep workflow has an unconditional machine-enforced suspension before checkout', () => {
  const workflow = readFileSync('.github/workflows/deep-monitor.yml', 'utf8');
  assert.match(workflow, /monitor:\r?\n\s+if: \$\{\{ false \}\}/);
  assert.doesNotMatch(workflow, /\n\s+schedule:/);
});

test('current FAILED and PARTIAL are distinct from stale and unconfigured observations', () => {
  const rows = [
    {status:'FAILED',last_checked_at:'2026-10-20T11:59:00Z'},
    {status:'PARTIAL',last_checked_at:'2026-10-20T11:59:00Z'},
    {status:'FAILED',last_checked_at:'2026-10-19T11:59:00Z'},
    {status:'PARTIAL',in_scope:false,last_checked_at:'2026-10-20T11:59:00Z',observation_status:'no_official_site',last_success_at:'2026-10-20T11:59:00Z'},
    {status:'OK',last_checked_at:'2026-10-20T11:59:00Z',last_success_at:'2026-10-20T11:59:00Z',last_error_at:'2026-10-19T11:59:00Z'}
  ];
  const h=context.MozesCheckpointHealth({checkpoint_at:'2026-10-20T11:59:00Z',generated_at:'2026-10-20T11:59:00Z',health:{source_freshness:rows}},now);
  assert.equal(h.current_failed_sources,1);
  assert.equal(h.current_partial_sources,1);
  assert.equal(h.stale_sources,1);
  assert.equal(h.unconfigured_observations,1);
  assert.equal(h.failed_sources,3);
  assert.equal(h.sources_stale,false);
  assert.equal(h.publication_stale,false);
});

test('fresh missing-source observations cannot manufacture a successful source scan', () => {
  const h=context.MozesCheckpointHealth({health:{source_freshness:[{status:'PARTIAL',observation_status:'no_rss_found',last_checked_at:'2026-10-20T11:59:00Z',last_success_at:'2026-10-20T11:59:00Z'}]}},now);
  assert.equal(h.source_scan_at,null);
  assert.equal(h.sources_stale,true);
  assert.equal(h.current_partial_sources,1);
});

test('banner keeps coverage gaps red while identifying recovered publication and bounded IR', () => {
  const c=vm.createContext({D:{},esc:String,MozesCheckpointHealth:()=>({
    stale:false,sources_stale:false,publication_stale:false,failed_sources:2,stale_sources:0,
    current_failed_sources:1,current_partial_sources:1,unconfigured_observations:60,
    ir_coverage:{configured_count:41,priority_count:40,rotating_count:1,rotating_per_cycle:8,
      configured_without_observation:['TYRA'],watch_without_configured_site:156},
    checkpoint_at:'2026-10-20T11:59:00Z',age_minutes:1,publication_at:'2026-10-20T11:59:30Z'
  })});
  const app=readFileSync('web/app.js','utf8');
  vm.runInContext(app.slice(app.indexOf('function checkpointBanner()'),app.indexOf('function render(){')),c);
  const html=c.checkpointBanner();
  assert.match(html,/red-note/);
  assert.match(html,/פרסום: מעודכן/);
  assert.match(html,/מוגדרות שטרם נבדקו: 1/);
  assert.match(html,/עדיפות: 40/);
  assert.match(html,/בסבב: 1/);
  assert.match(html,/ללא אתר IR מוגדר: 156/);
  assert.match(html,/תצפיות מחוץ לתצורת המקורות: 60/);
});

test('forty-minute data is delayed even inside the unchanged hard freshness guard', () => {
  const data={checkpoint_at:'2026-10-20T11:20:00Z',generated_at:'2026-10-20T11:59:00Z'};
  const h=context.MozesCheckpointHealth(data,now);
  assert.equal(h.stale,false);
  assert.equal(h.publication_delayed,true);
  assert.equal(h.checkpoint_to_export_minutes,39);
  assert.equal(h.publication_stale,false);
  for(const [checkpoint_at,delayed] of [['2026-10-20T11:40:01Z',false],['2026-10-20T11:40:00Z',true]])
    assert.equal(context.MozesCheckpointHealth({checkpoint_at},now).publication_delayed,delayed);
});
