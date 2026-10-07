// Public, bounded alert snapshots. The serialized hot workflow is the only writer.
export const STORE_FEED = `INSERT INTO alert_feed(id,sequence,generated_at,revision,payload,received_at)
  VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET sequence=excluded.sequence,
  generated_at=excluded.generated_at,revision=excluded.revision,payload=excluded.payload,
  received_at=excluded.received_at WHERE excluded.sequence>alert_feed.sequence OR
  (excluded.sequence=alert_feed.sequence AND excluded.generated_at>alert_feed.generated_at)`;
const MAX_BYTES = 256 * 1024;
const CHANNELS = new Set(["email", "ntfy", "webhook", "log"]);
const STATES = new Set(["pending", "sending", "sent", "failed", "dead"]);
const TEXT_FIELDS = { change_id: 64, created_at: 50, ticker: 12, change_type: 80,
  source_type: 40, source_url: 4000, published_at: 50, detected_at: 50, headline: 1200,
  summary: 1200, verification_state: 80 };

function headers(env, extra = {}) {
  return { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store",
    "Access-Control-Allow-Origin": env.SITE_ORIGIN || "https://mozes2024.github.io",
    "Vary": "Origin", "X-Content-Type-Options": "nosniff", ...extra };
}
function response(env, value, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: headers(env) });
}
function httpUrl(value, httpsOnly = false) {
  try {
    const url = new URL(value);
    return (httpsOnly ? url.protocol === "https:" : ["https:", "http:"].includes(url.protocol)) &&
      !url.username && !url.password;
  } catch { return false; }
}

export function publicFeed(value) {
  if (value?.schema !== 1 || !Number.isSafeInteger(value.sequence) || value.sequence < 1 ||
      !/^[a-f0-9]{64}$/.test(value.revision || "") ||
      !/^\d{4}-\d{2}-\d{2}T/.test(value.generated_at || "") ||
      !Number.isFinite(Date.parse(value.generated_at)) ||
      !Array.isArray(value.alerts) || value.alerts.length > 100) throw Error("invalid feed");
  const alerts = value.alerts.map((row) => {
    if (!row || !/^CHG-[a-f0-9]+$/.test(row.change_id || "") ||
        !["P1", "P2", "P3"].includes(row.priority) ||
        !["positive", "negative", "mixed", "unknown"].includes(row.polarity)) throw Error("invalid alert");
    const out = { priority: row.priority, polarity: row.polarity,
      watched: row.watched === true, delivered: row.delivered === true };
    for (const [key, maximum] of Object.entries(TEXT_FIELDS)) {
      if (row[key] != null && (typeof row[key] !== "string" || row[key].length > maximum)) throw Error("invalid text");
      out[key] = row[key] ?? null;
    }
    if (out.source_url && !httpUrl(out.source_url)) throw Error("invalid source URL");
    out.corroborated_by = Array.isArray(row.corroborated_by)
      ? row.corroborated_by.slice(0, 20).map(x => String(x).slice(0, 40)) : [];
    out.delivery = {};
    for (const [channel, item] of Object.entries(row.delivery || {})) {
      if (!CHANNELS.has(channel) || !STATES.has(item?.status) ||
          !Number.isSafeInteger(item.attempts) || item.attempts < 0) throw Error("invalid delivery");
      out.delivery[channel] = { status: item.status, attempts: item.attempts,
        sent_at: typeof item.sent_at === "string" ? item.sent_at.slice(0, 50) : null };
    }
    out.explanation = null;
    const analysis = row.explanation;
    if (analysis) {
      const fields = { summary_he: 500, why_he: 500, source_url: 4000, source_hash: 64,
        analyzed_at: 50, verification_state: 80, ai_model: 120 };
      if (!["source_text", "feed_summary", "headline_only", "structured_source"].includes(analysis.basis) ||
          !["source-rules-v1", "source-ai-v1"].includes(analysis.method) ||
          !["complete", "retry", "unavailable"].includes(analysis.status)) throw Error("invalid explanation");
      out.explanation = { basis: analysis.basis, method: analysis.method, status: analysis.status,
        ai_status: ["disabled", "unconfigured", "used", "fallback"].includes(analysis.ai_status) ? analysis.ai_status : "disabled" };
      out.explanation.source_polarity = ["positive", "negative", "mixed", "unknown"].includes(analysis.source_polarity)
        ? analysis.source_polarity : "unknown";
      for (const [field, max] of Object.entries(fields)) {
        if (analysis[field] != null && (typeof analysis[field] !== "string" || analysis[field].length > max)) throw Error("invalid explanation text");
        out.explanation[field] = analysis[field] ?? null;
      }
      out.explanation.missing_he = (Array.isArray(analysis.missing_he) ? analysis.missing_he : [])
        .slice(0, 4).map(x => String(x).slice(0, 400));
      out.explanation.evidence = (Array.isArray(analysis.evidence) ? analysis.evidence : []).slice(0, 1).map(item => {
        if (!httpUrl(item.url) || typeof item.quote !== "string") throw Error("invalid evidence");
        return { id: "source-1", quote: item.quote.slice(0, 600), url: item.url, topic: String(item.topic || "").slice(0, 40) };
      });
    }
    return out;
  });
  const config = value.alert_config || {};
  if (config.feed_url && !httpUrl(config.feed_url, true)) throw Error("invalid feed URL");
  return { schema: 1, sequence: value.sequence, revision: value.revision,
    generated_at: new Date(value.generated_at).toISOString(), alerts,
    truncated: value.truncated === true,
    alert_config: { enabled_channels: (Array.isArray(config.enabled_channels) ? config.enabled_channels : [])
      .filter(x => CHANNELS.has(x) && x !== "log"), feed_url: config.feed_url || null } };
}

async function readBounded(request) {
  if (Number(request.headers.get("Content-Length")) > MAX_BYTES) throw Error("too large");
  const reader = request.body?.getReader();
  if (!reader) throw Error("invalid body");
  let size = 0, chunks = [];
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > MAX_BYTES) { await reader.cancel(); throw Error("too large"); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
}

export async function handleAlertFeed(request, env, now = new Date()) {
  if (request.method === "OPTIONS") return new Response(null, { status: 204,
    headers: headers(env, { "Access-Control-Allow-Methods": "GET, OPTIONS" }) });
  if (!["GET", "POST"].includes(request.method)) return response(env, { error: "method not allowed" }, 405);
  if (request.method === "POST" && (!env.ALERT_FEED_TOKEN ||
      request.headers.get("Authorization") !== `Bearer ${env.ALERT_FEED_TOKEN}`)) {
    return response(env, { error: "unauthorized" }, 401);
  }
  if (!env.ALERTS_DB) return response(env, { error: "alert storage not configured" }, 503);
  try {
    if (request.method === "GET") {
      const stored = await env.ALERTS_DB.prepare("SELECT payload,received_at FROM alert_feed WHERE id=1").first();
      if (!stored) return response(env, { error: "alert feed not initialized" }, 503);
      return response(env, { ...JSON.parse(stored.payload), received_at: stored.received_at });
    }
    if (!request.headers.get("Content-Type")?.startsWith("application/json")) {
      return response(env, { error: "JSON required" }, 415);
    }
    let feed;
    try { feed = publicFeed(await readBounded(request)); }
    catch (error) { return response(env, { error: "invalid alert feed" }, error.message === "too large" ? 413 : 400); }
    if (Date.parse(feed.generated_at) > now.getTime() + 5 * 60_000) {
      return response(env, { error: "future feed timestamp" }, 400);
    }
    const result = await env.ALERTS_DB.prepare(STORE_FEED).bind(feed.sequence, feed.generated_at,
      feed.revision, JSON.stringify(feed), now.toISOString()).run();
    if (!result.meta?.changes) {
      const stored = await env.ALERTS_DB.prepare("SELECT sequence,revision FROM alert_feed WHERE id=1").first();
      if (stored?.sequence === feed.sequence && stored.revision === feed.revision) {
        return response(env, { ok: true, duplicate: true });
      }
      return response(env, { error: "superseded feed" }, 409);
    }
    return response(env, { ok: true, revision: feed.revision });
  } catch { return response(env, { error: "alert storage unavailable" }, 503); }
}
