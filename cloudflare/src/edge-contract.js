// Sync token scope: issuer sync, enrichment ACK, and private event trace only.
export const authorized = (request, env) => Boolean(env.EDGE_SYNC_TOKEN) &&
  request.headers.get("Authorization") === `Bearer ${env.EDGE_SYNC_TOKEN}`;
export const validEdgeId = id => /^EDGE-[a-f0-9]{24}$/.test(id || "");

export async function readJson(request, limit) {
  if (Number(request.headers.get("Content-Length") || 0) > limit) throw new Response("Too large", { status: 413 });
  const reader = request.body?.getReader();
  if (!reader) throw new Response("Invalid body", { status: 400 });
  const chunks = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > limit) { await reader.cancel(); throw new Response("Too large", { status: 413 }); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  try { return JSON.parse(new TextDecoder().decode(bytes)); }
  catch { throw new Response("Invalid JSON", { status: 400 }); }
}

export async function acknowledge(request, env, now = new Date().toISOString()) {
  if (request.method !== "POST" || !authorized(request, env)) return new Response("Unauthorized", { status: 401 });
  let body;
  try { body = await readJson(request, 16384); } catch (response) { return response; }
  if (!validEdgeId(body?.edge_event_id) || body.status !== "completed" ||
      !/^CHG-[a-f0-9]{24}$/.test(body.change_id || "") || !/^\d{1,20}$/.test(body.github_run_id || "") ||
      (body.catalyst_id && !/^CAT-[a-f0-9]{24}$/.test(body.catalyst_id)) ||
      !Number.isFinite(Date.parse(body.completed_at)) || Date.parse(body.completed_at) > Date.parse(now) + 300000 ||
      !Array.isArray(body.alert_ids) || body.alert_ids.length > 20 || body.alert_ids.some(id => !/^ALT-[a-f0-9]{24}$/.test(id)) ||
      !Array.isArray(body.delivery) || body.delivery.length > 20 || body.delivery.some(row =>
        !body.alert_ids.includes(row?.alert_id) || !["pending", "sending", "sent", "failed", "dead"].includes(row?.status)))
    return new Response("Invalid ACK", { status: 400 });
  const db = env.ALERTS_DB;
  const event = await db.prepare("SELECT * FROM edge_events WHERE event_id=?").bind(body.edge_event_id).first();
  if (!event) return new Response("Unknown event", { status: 404 });
  if (event.analysis_status !== "complete" || !event.material || Date.parse(body.completed_at) < Date.parse(event.first_seen_at))
    return new Response("Event not ready for ACK", { status: 409 });
  if (!event.enrichment_ack_at) {
    const delivery = body.delivery.map(row => ({ alert_id: row.alert_id, status: row.status,
      sent_at: Number.isFinite(Date.parse(row.sent_at)) ? row.sent_at : null }));
    await db.prepare("UPDATE edge_events SET enrichment_ack_at=?,enrichment_completed_at=?,enrichment_change_id=?,enrichment_github_run_id=?,enrichment_alert_ids_json=?,enrichment_delivery_json=?,last_enrichment_error=NULL,enrichment_retry_at=NULL WHERE event_id=? AND enrichment_ack_at IS NULL")
      .bind(now, body.completed_at, body.change_id, body.github_run_id, JSON.stringify(body.alert_ids), JSON.stringify(delivery), body.edge_event_id).run();
  }
  const stored = await db.prepare("SELECT * FROM edge_events WHERE event_id=?").bind(body.edge_event_id).first();
  if (stored.enrichment_change_id !== body.change_id) return new Response("Conflicting ACK", { status: 409 });
  await db.prepare("UPDATE edge_events SET lifecycle='PUBLISHED' WHERE event_id=?").bind(body.edge_event_id).run();
  const candidate = await db.prepare('SELECT * FROM edge_candidates WHERE candidate_id=?').bind(body.edge_event_id).first();
  if(candidate && !candidate.completed_at){
    const history=JSON.parse(candidate.history_json);
    history.push(...['ENRICHED','PUBLISHED'].map(state=>({state,at:stored.enrichment_ack_at})));
    await db.prepare("UPDATE edge_candidates SET lifecycle='PUBLISHED',suppression_reason=NULL,completed_at=?,github_run_id=?,change_id=?,catalyst_id=?,history_json=? WHERE candidate_id=? AND completed_at IS NULL")
      .bind(stored.enrichment_ack_at,body.github_run_id,body.change_id,body.catalyst_id||null,JSON.stringify(history.slice(-10)),body.edge_event_id).run();
  }
  return Response.json({ ok: true, edge_event_id: body.edge_event_id, status: "completed", enrichment_ack_at: stored.enrichment_ack_at });
}

export async function eventTrace(request, env) {
  if (request.method !== "GET" || !authorized(request, env)) return new Response("Unauthorized", { status: 401 });
  const id = new URL(request.url).searchParams.get("id");
  if (!validEdgeId(id)) return new Response("Invalid ID", { status: 400 });
  const row = await env.ALERTS_DB.prepare("SELECT * FROM edge_events WHERE event_id=?").bind(id).first();
  if (!row) return new Response("Unknown event", { status: 404 });
  return Response.json({ ...row, edge_event_id: row.event_id,
    enrichment_status: row.enrichment_ack_at ? "completed" : row.enrichment_dispatch_at ? "awaiting_ack" : "pending" });
}
