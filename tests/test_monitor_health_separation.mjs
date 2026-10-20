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
