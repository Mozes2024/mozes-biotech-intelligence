// Cron is only a watchdog for the Durable Object's recurring Stage-0 alarm.

import { handleAlertFeed } from "./alert-feed.js";
import { acknowledge, eventTrace } from "./edge-contract.js";
import { edgeMetrics } from "./edge-metrics.js";
import {candidates,candidateAck,investigate,candidateStats} from './candidate-contract.js';
export { HotEdge } from "./hot-edge.js";

export default {
  async scheduled(controller, env, ctx) {
    const stub = env.HOT_EDGE.get(env.HOT_EDGE.idFromName("global"));
    ctx.waitUntil(stub.fetch("https://edge.internal/rearm"));
  },
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === "/alerts") return handleAlertFeed(request, env);
    if (path === "/edge/sync") return syncIssuers(request, env);
    if (path === "/edge/ack") return acknowledge(request, env);
    if (path === "/edge/candidates") return candidates(request, env);
    if (path === "/edge/candidate/ack") return candidateAck(request, env);
    if (path === "/edge/investigate") return investigate(request, env);
    if (path === "/edge/event") return eventTrace(request, env);
    if (path === "/edge/metrics") return publicHealth(await edgeMetrics(env.ALERTS_DB), env);
    if (path === "/edge/health") {
      const stub = env.HOT_EDGE.get(env.HOT_EDGE.idFromName("global"));
      const health = await (await stub.fetch("https://edge.internal/health")).json();
      const sources = Object.values(health.sources || {});
      const failed = sources.filter(row => row.status === "FAILED").length;
      const backlog = await env.ALERTS_DB.prepare("SELECT COUNT(*) AS events,SUM(CASE WHEN analysis_status<>'complete' THEN 1 ELSE 0 END) AS analysis_pending,SUM(CASE WHEN material=1 AND enrichment_ack_at IS NULL THEN 1 ELSE 0 END) AS enrichment_pending FROM edge_events").first();
      return publicHealth({ ...health, status: !sources.length ? "UNKNOWN" : failed === sources.length ? "FAILED" : failed ? "PARTIAL" : "OK",
        stage0_delivery_enabled: Boolean(env.NTFY_URL), enrichment_enabled: env.EDGE_ENRICHMENT_ENABLED !== "0",
        backlog, clinical_operations:await candidateStats(env.ALERTS_DB), metrics: await edgeMetrics(env.ALERTS_DB) }, env);
    }
    return new Response(JSON.stringify({ ok: true, service: "mozes-hot-clock" }), {
      headers: { "content-type": "application/json" },
    });
  },
};

function publicHealth(body, env) {
  return Response.json(body, { headers: { "Cache-Control": "no-store", "Access-Control-Allow-Origin": env.SITE_ORIGIN || "https://mozes2024.github.io", Vary: "Origin" } });
}

export async function syncIssuers(request, env) {
  if (request.method !== "POST" || !env.EDGE_SYNC_TOKEN ||
      request.headers.get("Authorization") !== `Bearer ${env.EDGE_SYNC_TOKEN}`)
    return new Response("Unauthorized", { status: 401 });
  if (Number(request.headers.get("Content-Length") || 0) > 512 * 1024)
    return new Response("Too large", { status: 413 });
  const reader = request.body?.getReader();
  if (!reader) return new Response("Invalid body", { status: 400 });
  const chunks = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > 512 * 1024) { await reader.cancel(); return new Response("Too large", { status: 413 }); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  const raw = new TextDecoder().decode(bytes);
  let body;
  try { body = JSON.parse(raw); } catch { return new Response("Invalid JSON", { status: 400 }); }
  if (!Array.isArray(body?.issuers) || body.issuers.length > 5000 || !body.issuers.length)
    return new Response("Invalid universe", { status: 400 });
  const seen = new Set();
  for (const row of body.issuers) {
    if (!/^\d{1,10}$/.test(row?.cik || "") || !/^[A-Z][A-Z0-9]{0,9}$/.test(row?.ticker || "") ||
        typeof row.company !== "string" || row.company.length < 3 || row.company.length > 200 ||
        typeof row.source !== "string" || row.source.length > 80 ||
        !Number.isFinite(row.confidence) || !(row.confidence >= 0.85 && row.confidence <= 1) ||
        seen.has(String(Number(row.cik))))
      return new Response("Invalid issuer", { status: 400 });
    seen.add(String(Number(row.cik)));
    row.cik = String(Number(row.cik));
  }
  const now = new Date().toISOString();
  const db = env.ALERTS_DB;
  // A matching read is a valid linearization point for a write-free request.
  // Changed requests never use this read to plan writes: the batch below
  // rechecks the current database, including concurrent commits.
  const current = (await db.prepare("SELECT cik,ticker,company,confidence,source FROM edge_issuers WHERE active=1").all()).results;
  const byCik = new Map(current.map(row => [row.cik, row]));
  if (current.length === body.issuers.length && body.issuers.every(row => {
    const old = byCik.get(row.cik);
    return old && ['ticker','company','confidence','source'].every(key => old[key] === row[key]);
  })) return Response.json({ ok: true, count: body.issuers.length, changed: 0, unchanged: true });
  const snapshot = JSON.stringify(body.issuers);
  // Every gate and mutation executes inside one D1 transaction. No stale read
  // outside the batch can overwrite a concurrently committed partial snapshot.
  // Retain at least 95% of active identities (one removal is allowed for small
  // universes); additions cannot conceal a truncated/replaced universe.
  const input = `WITH incoming AS (SELECT json_extract(value,'$.cik') AS cik,
    json_extract(value,'$.ticker') AS ticker,json_extract(value,'$.company') AS company,
    json_extract(value,'$.confidence') AS confidence,json_extract(value,'$.source') AS source
    FROM json_each(?)), counts AS (SELECT COUNT(*) AS active,
    COALESCE(SUM(cik NOT IN (SELECT cik FROM incoming)),0) AS removed
    FROM edge_issuers WHERE active=1), gate AS
    (SELECT removed<=MAX(1,CAST(active*0.05 AS INTEGER)) AS accepted FROM counts) `;
  const results = await db.batch([
    db.prepare(input + "SELECT accepted FROM gate").bind(snapshot),
    db.prepare(input + `UPDATE edge_issuers SET active=0,updated_at=? WHERE active=1
      AND cik NOT IN (SELECT cik FROM incoming) AND (SELECT accepted FROM gate)`)
      .bind(snapshot, now),
    db.prepare(input + `INSERT INTO edge_issuers(cik,ticker,company,confidence,source,active,updated_at)
      SELECT cik,ticker,company,confidence,source,1,? FROM incoming
      WHERE (SELECT accepted FROM gate)
      ON CONFLICT(cik) DO UPDATE SET ticker=excluded.ticker,company=excluded.company,
      confidence=excluded.confidence,source=excluded.source,active=1,updated_at=excluded.updated_at
      WHERE edge_issuers.ticker IS NOT excluded.ticker OR edge_issuers.company IS NOT excluded.company
      OR edge_issuers.confidence IS NOT excluded.confidence OR edge_issuers.source IS NOT excluded.source
      OR edge_issuers.active<>1`).bind(snapshot, now),
  ]);
  if (!results[0].results[0].accepted)
    return new Response("Suspicious universe reduction", { status: 409 });
  const changed = results.slice(1).reduce((n, r) => n + (r.meta?.changes || 0), 0);
  return Response.json({ ok: true, count: body.issuers.length, changed, unchanged: changed === 0 });
}
