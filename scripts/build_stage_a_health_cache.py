"""Add a 300-second response cache for /edge/health and /edge/metrics.

Builds the reviewed Stage A watchdog candidate (the exact bundle deployed to
production on Oct 21, version a975437b) and injects only the D1 quota guard for
the two polling routes. Every other route, the idle watchdog, the scheduler,
the forward-only fence and the alert feed stay byte-identical to production.
This script never uploads or deploys anything.

Context: production serves /edge/health and /edge/metrics by recomputing
whole-history summary scans on every request. An unidentified 24/7 polling
client (~2,100 calls/day, ~41s cadence) drives most of the account's D1
rows_read against the free daily cap. Main's worker design solves the same
problem with EDGE_HEALTH_CACHE_SECONDS = 300, so a 300-second per-PoP response
cache aligns production with the reviewed design: health and metrics may be up
to five minutes stale; /alerts alert delivery is never cached; non-200
responses and non-GET requests always pass through.
"""
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    'stage_a_watchdog_builder', ROOT / 'scripts' / 'build_stage_a_watchdog.py')
_builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_builder)
build_stage_a = _builder.build

CACHE_HELPERS = '''// D1 quota guard: /edge/health and /edge/metrics recompute whole-history
// summary scans per request. Serve a 300-second per-PoP cached response so a
// polling client cannot exhaust the daily rows_read quota. Matches the
// reviewed EDGE_HEALTH_CACHE_SECONDS=300 semantics: health and metrics may be
// up to five minutes stale; /alerts alert delivery is never cached.
const HEALTH_CACHE_PATHS = new Set(["/edge/health", "/edge/metrics"]);
async function cachedHealth(request) {
  if (request.method === "GET") {
    const cached = await caches.default.match(request);
    if (cached) return cached;
  }
  return null;
}
function storeHealthCache(request, ctx, response) {
  if (request.method !== "GET" || response.status !== 200 || !ctx || !ctx.waitUntil) return;
  const copy = response.clone();
  const headers = new Headers(copy.headers);
  headers.set("Cache-Control", "public, max-age=300");
  ctx.waitUntil(caches.default.put(request, new Response(copy.body, { status: copy.status, headers })));
}

'''


def _replace_once(text, anchor, replacement):
    if text.count(anchor) != 1:
        raise RuntimeError(f'anchor must occur exactly once: {anchor[:80]!r}')
    return text.replace(anchor, replacement)


def build(destination: Path) -> Path:
    destination = Path(destination)
    build_stage_a(destination)
    path = destination / 'cloudflare' / 'src' / 'index.js'
    text = path.read_text(encoding='utf-8')

    # 1. Pass the execution context through fetch so cache writes can use waitUntil.
    text = _replace_once(text, '  async fetch(request, env) {', '  async fetch(request, env, ctx) {')

    # 2. Consult the cache before any D1 or Durable Object work.
    text = _replace_once(
        text,
        '    const path = new URL(request.url).pathname;\n',
        '    const path = new URL(request.url).pathname;\n'
        '    if (HEALTH_CACHE_PATHS.has(path)) {\n'
        '      const hit = await cachedHealth(request);\n'
        '      if (hit) return hit;\n'
        '    }\n')

    # 3. /edge/metrics: cache the direct D1 response.
    text = _replace_once(
        text,
        '    if (path === "/edge/metrics") return publicHealth(await edgeMetrics(env.ALERTS_DB), env);',
        '    if (path === "/edge/metrics") {\n'
        '      const response = publicHealth(await edgeMetrics(env.ALERTS_DB), env);\n'
        '      storeHealthCache(request, ctx, response);\n'
        '      return response;\n'
        '    }')

    # 4. /edge/health: cache the assembled health response.
    text = _replace_once(text, '      return publicHealth({ ...health,', '      const response = publicHealth({ ...health,')
    text = _replace_once(
        text,
        '        backlog, clinical_operations:await candidateStats(env.ALERTS_DB), metrics: await edgeMetrics(env.ALERTS_DB) }, env);',
        '        backlog, clinical_operations:await candidateStats(env.ALERTS_DB), metrics: await edgeMetrics(env.ALERTS_DB) }, env);\n'
        '      storeHealthCache(request, ctx, response);\n'
        '      return response;')

    # 5. Inject helpers above publicHealth.
    text = _replace_once(text, 'function publicHealth(body, env) {', CACHE_HELPERS + 'function publicHealth(body, env) {')
    path.write_text(text, encoding='utf-8')
    return destination


if __name__ == '__main__':
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/tmp/stage-a-health-cache')
    print(build(target))
