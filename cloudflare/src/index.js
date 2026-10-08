// Cron is only a watchdog for the Durable Object's recurring Stage-0 alarm.

import { handleAlertFeed } from "./alert-feed.js";
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
    if (path === "/edge/health") {
      const stub = env.HOT_EDGE.get(env.HOT_EDGE.idFromName("global"));
      return stub.fetch("https://edge.internal/health");
    }
    return new Response(JSON.stringify({ ok: true, service: "mozes-hot-clock" }), {
      headers: { "content-type": "application/json" },
    });
  },
};

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
        !(row.confidence >= 0.85 && row.confidence <= 1) || seen.has(row.cik))
      return new Response("Invalid issuer", { status: 400 });
    seen.add(row.cik);
  }
  const now = new Date().toISOString();
  const db = env.ALERTS_DB;
  const statements = [db.prepare("UPDATE edge_issuers SET active=0")];
  const upsert = db.prepare("INSERT INTO edge_issuers(cik,ticker,company,confidence,source,active,updated_at) VALUES(?,?,?,?,?,1,?) ON CONFLICT(cik) DO UPDATE SET ticker=excluded.ticker,company=excluded.company,confidence=excluded.confidence,source=excluded.source,active=1,updated_at=excluded.updated_at");
  for (const row of body.issuers)
    statements.push(upsert.bind(row.cik, row.ticker, row.company, row.confidence, row.source, now));
  await db.batch(statements);
  return Response.json({ ok: true, count: body.issuers.length });
}
