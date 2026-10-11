// Authenticated producer evidence only. Never an ACK, restore or dispatch permit.
export const RECEIPT_KEY = 'monitor_checkpoint_receipt_v1';
const REPO = 'Mozes2024/mozes-biotech-intelligence';
const stamp = value => typeof value === 'string' && /(?:Z|\+00:00)$/.test(value) ? Date.parse(value) : NaN;
const fields = ['schema','repository','workflow','run_id','run_attempt','head_sha',
  'artifact_id','artifact_created_at','checkpoint_at','db_sha256','proof'];

export async function checkpointFeedback(request, storage, env, now = Date.now()) {
  if (env.EDGE_CHECKPOINT_FEEDBACK_ENABLED !== '1') return new Response(null, {status:404});
  if (request.method !== 'POST' || !env.EDGE_SYNC_TOKEN ||
      request.headers.get('Authorization') !== `Bearer ${env.EDGE_SYNC_TOKEN}`)
    return new Response(null, {status:401});
  let body;
  try {
    const reader = request.body?.getReader();
    if (!reader) return new Response(null, {status:400});
    let chunks = [], size = 0;
    while (true) {
      const {done,value} = await reader.read();
      if (done) break;
      size += value.length;
      if (size > 4096) { await reader.cancel(); return new Response(null, {status:413}); }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk,offset); offset += chunk.length; }
    body = JSON.parse(new TextDecoder().decode(bytes));
  } catch { return new Response(null, {status:400}); }
  const at = stamp(body?.checkpoint_at), uploaded = stamp(body?.artifact_created_at);
  if (!body || Object.keys(body).length !== fields.length || fields.some(k=>!(k in body)) ||
      body.schema !== 1 || body.repository !== REPO || body.workflow !== 'lightweight-monitor.yml' ||
      body.run_attempt !== 1 || typeof body.run_id !== 'string' || !/^[1-9]\d{0,19}$/.test(body.run_id) ||
      !Number.isSafeInteger(body.artifact_id) || body.artifact_id <= 0 ||
      !/^[a-f0-9]{40}$/.test(body.head_sha || '') || !/^[a-f0-9]{64}$/.test(body.db_sha256 || '') ||
      body.proof !== 'actions_download_sha256_sqlite_v1' || !Number.isFinite(at) ||
      !Number.isFinite(uploaded) || at > uploaded || uploaded > now || now - at > 2700000)
    return new Response(null, {status:422});
  // Caller serializes with the existing singleton alarm/sync queue. One durable
  // write retains the original authenticated receipt; replay cannot refresh age.
  const prior = await storage.get(RECEIPT_KEY);
  if (prior) {
    if (!Number.isFinite(stamp(prior.checkpoint_at)) || !Number.isFinite(stamp(prior.artifact_created_at)))
      return new Response(null, {status:409});
    const same = fields.every(k=>prior[k] === body[k]);
    if (same) return Response.json({ok:true,status:'duplicate'});
    if (at <= stamp(prior.checkpoint_at) || uploaded <= stamp(prior.artifact_created_at) ||
        body.artifact_id === prior.artifact_id || body.run_id === prior.run_id)
      return new Response(null, {status:409});
  }
  await storage.put(RECEIPT_KEY, Object.fromEntries(fields.map(k=>[k,body[k]])));
  return Response.json({ok:true,status:'checkpointed'});
}
