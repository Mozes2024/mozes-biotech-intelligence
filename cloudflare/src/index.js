// Cron is only a watchdog for the Durable Object's recurring Stage-0 alarm.

export { HotEdge } from "./hot-edge.js";

export default {
  async scheduled(controller, env, ctx) {
    const stub = env.HOT_EDGE.get(env.HOT_EDGE.idFromName("global"));
    ctx.waitUntil(stub.fetch("https://edge.internal/rearm"));
  },
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === "/edge/sync") return syncIssuers(request, env);
    const stub = env.HOT_EDGE?.get(env.HOT_EDGE.idFromName("global"));
    if (["/alerts","/edge/ack","/edge/candidates","/edge/candidate/ack","/edge/investigate","/edge/event"].includes(path)) return stub.fetch(request);
    if (path === "/edge/metrics") return publicHealth((await (await stub.fetch("https://edge.internal/summaries")).json()).metrics, env);
    if (path === "/edge/health") {
      const health = await (await stub.fetch("https://edge.internal/health")).json();
      const sources = Object.values(health.sources || {});
      const failed = sources.filter(row => row.status === "FAILED").length;
      const summary = await (await stub.fetch("https://edge.internal/summaries")).json();
      return publicHealth({ ...health, status: !sources.length ? "UNKNOWN" : failed === sources.length ? "FAILED" : failed ? "PARTIAL" : "OK",
        stage0_delivery_enabled: Boolean(env.NTFY_URL), enrichment_enabled: env.EDGE_ENRICHMENT_ENABLED !== "0",
        ...summary }, env);
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
        !Number.isFinite(row.confidence) || !(row.confidence >= 0.85 && row.confidence <= 1) || seen.has(String(Number(row.cik))))
      return new Response("Invalid issuer", { status: 400 });
    seen.add(String(Number(row.cik)));
  }
  const stub=env.HOT_EDGE.get(env.HOT_EDGE.idFromName('global'));
  return stub.fetch(new Request('https://edge.internal/sync',{method:'POST',body:JSON.stringify(body)}));
}
