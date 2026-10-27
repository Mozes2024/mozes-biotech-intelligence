// Runtime behavior tests for the Stage A health-cache guard.
// Executes the actual built candidate under `wrangler dev` - the real workerd
// runtime, Cache API, D1 and Durable Object implementations that production
// runs on - not source assertions. Covers: cache hit/miss, query-string and
// host keying, non-GET bypass, non-200 passthrough, degraded-status caching
// semantics, CORS preservation, and /alerts staying uncached.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, execFileSync } from 'node:child_process';

const repoRoot = new URL('..', import.meta.url).pathname;
const buildRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'health-cache-'));
execFileSync('python3', [path.join(repoRoot, 'scripts', 'build_stage_a_health_cache.py'),
  path.join(buildRoot, 'candidate')], { stdio: 'pipe' });
const cf = path.join(buildRoot, 'candidate', 'cloudflare');

const env = { ...process.env, CI: '1' };
// Prefer the repo-pinned wrangler (cloudflare devDependency) over npx so CI
// runs the same wrangler/workerd version the deploy workflows use.
const localWrangler = path.join(repoRoot, 'cloudflare', 'node_modules', '.bin', 'wrangler');
const useLocal = fs.existsSync(localWrangler);
const wcmd = useLocal ? localWrangler : 'npx';
const wargs = (a) => (useLocal ? a : ['wrangler', ...a]);
const wrangler = (args, options = {}) =>
  execFileSync(wcmd, [...wargs(args), '--config', 'wrangler.toml'], { cwd: cf, env, stdio: 'pipe', ...options });
wrangler(['d1', 'migrations', 'apply', 'mozes-alerts', '--local']);
const d1 = (sql) => wrangler(['d1', 'execute', 'mozes-alerts', '--local', '--command', sql]);

const PORT = 8730 + Math.floor(Math.random() * 200);
const BASE = `http://127.0.0.1:${PORT}`;
// EDGE_FORWARD_ONLY_FROM is a dashboard-set production var (keep_vars=true),
// required by the forward-only fence in /edge/health; seeded here for local dev.
const dev = spawn(wcmd, wargs(['dev', '--local', '--port', String(PORT), '--config', 'wrangler.toml',
  '--var', 'EDGE_FORWARD_ONLY_FROM:2026-10-21T00:00:00Z']),
  { cwd: cf, env, stdio: 'pipe' });
let devLog = '';
// Local Wrangler/D1 CLI operations can close idle keep-alive sockets. Cache
// assertions concern workerd's Cache API across requests, not socket reuse.
// Use fresh HTTP connections, retaining every status/body/cache assertion.
const fetch = async (url, init = {}) => {
  try {
    return await globalThis.fetch(url, { ...init, headers: { ...init.headers, Connection: 'close' } });
  } catch (cause) {
    throw new Error(`Local workerd request failed: ${init.method || 'GET'} ${url}`, { cause });
  }
};
dev.stdout.on('data', (chunk) => { devLog += chunk; });
dev.stderr.on('data', (chunk) => { devLog += chunk; });
test.after(() => { dev.kill('SIGINT'); });

const readyDeadline = Date.now() + 90000;
for (;;) {
  try {
    const r = await fetch(`${BASE}/`);
    if (r.ok) break;
  } catch { /* not up yet */ }
  assert.ok(Date.now() < readyDeadline, `wrangler dev did not start. Log:\n${devLog}`);
  await new Promise((resolve) => setTimeout(resolve, 500));
}

let seq = 0;
const insertEvent = () => d1(
  `INSERT INTO edge_events (event_id, source, source_url, ticker, first_seen_at, polarity, material, accepted_at, published_at) ` +
  `VALUES ('evt-${++seq}', 'sec', 'https://example.com/x', 'AAA', strftime('%Y-%m-%dT%H:%M:%SZ','now'), 'POSITIVE', 1, strftime('%Y-%m-%dT%H:%M:%SZ','now'), strftime('%Y-%m-%dT%H:%M:%SZ','now'))`);
const feed = (n) => d1(
  `INSERT OR REPLACE INTO alert_feed (id, sequence, generated_at, revision, payload, received_at) ` +
  `VALUES (1, ${n}, datetime('now'), 'rev', '{"seq":${n}}', datetime('now'))`);
const rows24h = async (response) => (await response.json()).windows['24h'].rows;

test('health cache runtime behavior', { timeout: 120000 }, async () => {
  // 1. First GET is a miss: recomputed, original no-store header, CORS origin preserved.
  let r = await fetch(`${BASE}/edge/metrics`);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get('Cache-Control'), 'no-store');
  assert.equal(r.headers.get('Access-Control-Allow-Origin'), 'https://mozes2024.github.io');
  assert.equal(await rows24h(r), 0);

  // 2. Same URL after a data change is served from cache: the stale payload
  //    proves the hit, and the stored copy carries max-age=300 with CORS intact.
  await insertEvent();
  r = await fetch(`${BASE}/edge/metrics`);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get('Cache-Control'), 'public, max-age=300');
  assert.equal(r.headers.get('Access-Control-Allow-Origin'), 'https://mozes2024.github.io');
  assert.equal(await rows24h(r), 0);

  // 3. Query string participates in the cache key (workers.dev default):
  //    an explicit cache-buster recomputes honestly.
  r = await fetch(`${BASE}/edge/metrics?cb=1`);
  assert.equal(await rows24h(r), 1);

  // 4. Host participates in the key: no cross-host cache poisoning.
  r = await fetch(`${BASE}/edge/metrics`.replace('127.0.0.1', 'localhost'));
  assert.equal(await rows24h(r), 1);

  // 5. Non-GET bypasses the cache and is never stored: POST recomputes,
  //    and the original GET entry survives it untouched.
  await insertEvent();
  r = await fetch(`${BASE}/edge/metrics`, { method: 'POST' });
  assert.equal(await rows24h(r), 2);
  r = await fetch(`${BASE}/edge/metrics`);
  assert.equal(await rows24h(r), 0);

  // 6. Non-200 responses are never cached: a failure, then recovery recomputes.
  d1('ALTER TABLE edge_events RENAME TO edge_events_hidden');
  r = await fetch(`${BASE}/edge/metrics?broken=1`);
  assert.equal(r.status, 500);
  d1('ALTER TABLE edge_events_hidden RENAME TO edge_events');
  r = await fetch(`${BASE}/edge/metrics?broken=1`);
  assert.equal(r.status, 200);

  // 7. /edge/health is cached identically: equal generated_at across the two
  //    calls proves the second came from the cache without recomputing.
  r = await fetch(`${BASE}/edge/health`);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get('Cache-Control'), 'no-store');
  const h1 = await r.json();
  r = await fetch(`${BASE}/edge/health`);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get('Cache-Control'), 'public, max-age=300');
  const h2 = await r.json();
  assert.equal(h2.metrics.generated_at, h1.metrics.generated_at);

  // 8. Degraded statuses (PARTIAL/FAILED) are 200 responses and ARE cached for
  //    up to 300s - documented semantics matching the reviewed
  //    EDGE_HEALTH_CACHE_SECONDS=300 design: status round-trips unchanged.
  assert.ok(['OK', 'PARTIAL', 'FAILED', 'UNKNOWN'].includes(h1.status));
  assert.equal(h2.status, h1.status);

  // 9. /alerts alert delivery is never cached: payload changes are live.
  await feed(1);
  r = await fetch(`${BASE}/alerts`);
  assert.equal((await r.json()).seq, 1);
  await feed(2);
  r = await fetch(`${BASE}/alerts`);
  assert.equal(r.status, 200);
  assert.equal((await r.json()).seq, 2);
  assert.notEqual(r.headers.get('Cache-Control'), 'public, max-age=300');
});
