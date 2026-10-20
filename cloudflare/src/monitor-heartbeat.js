// A quiet-source watchdog, owned by the existing singleton DO. No D1 writes.
const REPO = 'Mozes2024/mozes-biotech-intelligence';
const WORKFLOW = 'lightweight-monitor.yml';
const ACTIVE = ['queued', 'in_progress', 'waiting', 'pending', 'requested'];
const CHECK_INTERVAL = 600000, THROTTLE = 600000;
export const DISPATCH_KEY = 'monitor_dispatch_attempt_at';
const RESERVATION_KEY = 'monitor_dispatch_checkpoint';
const stamp = value => typeof value === 'string' && /(?:Z|\+00:00)$/.test(value) ? Date.parse(value) : NaN;
class EvidenceError extends Error {}
const fail = category => { throw new EvidenceError(category); };

async function api(env, path, body) {
  let response;
  try {
    response = await fetch(`https://api.github.com/repos/${REPO}/actions/${path}`, {
      method: body ? 'POST' : 'GET', redirect: 'error', signal: AbortSignal.timeout(8000),
      headers: {Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: 'application/vnd.github+json',
        'User-Agent': 'mozes-hot-edge', 'X-GitHub-Api-Version': '2022-11-28',
        ...(body ? {'Content-Type': 'application/json'} : {})},
      ...(body ? {body: JSON.stringify(body)} : {}),
    });
  } catch { fail('network_or_timeout'); }
  if (body) { if (response.status !== 204) fail(`http_${response.status}`); return; }
  if (response.status !== 200) fail(`http_${response.status}`);
  // Bound the stream, including responses without Content-Length.
  const reader = response.body?.getReader();
  if (!reader) fail('invalid_response');
  let size = 0, chunks = [];
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    size += value.length;
    if (size > 1048576) { await reader.cancel(); fail('response_too_large'); }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  try { return JSON.parse(new TextDecoder().decode(bytes)); } catch { fail('invalid_response'); }
}

function collection(value, key) {
  if (!Number.isSafeInteger(value?.total_count) || !Array.isArray(value[key]) ||
      value.total_count < value[key].length) fail('incomplete_evidence');
  return value[key];
}

async function checkpoint(env, now) {
  let artifacts = [], complete = false;
  for (let page = 1; page <= 15; page++) {
    const result = await api(env, `artifacts?name=mozes-live-monitor&per_page=100&page=${page}`);
    const rows = collection(result, 'artifacts');
    artifacts.push(...rows);
    // Enumerate completely: never silently omit a newer deleted/expired candidate.
    if (artifacts.length === result.total_count) { complete = true; break; }
    if (!rows.length) fail('incomplete_evidence');
  }
  if (!complete) fail('inventory_bound');
  const candidates = artifacts.filter(a => a.name === 'mozes-live-monitor');
  if (!candidates.length) fail('checkpoint_missing');
  if (candidates.some(a => !Number.isFinite(stamp(a.created_at)))) fail('invalid_checkpoint');
  candidates.sort((a,b) => stamp(b.created_at) - stamp(a.created_at));
  const a = candidates[0];
  if (candidates[1]?.created_at === a.created_at) fail('ambiguous_checkpoint');
  if (a.expired !== false || !Number.isSafeInteger(a.id) || !Number.isSafeInteger(a.workflow_run?.id) ||
      !(a.size_in_bytes > 0) || !/^sha256:[a-f0-9]{64}$/.test(a.digest || '') ||
      a.workflow_run.head_branch !== 'main' || stamp(a.created_at) > now) fail('invalid_checkpoint');
  const run = await api(env, `runs/${a.workflow_run.id}`);
  if (run.id !== a.workflow_run.id || run.head_branch !== 'main' ||
      run.head_repository?.full_name !== REPO || run.name !== 'lightweight-live-monitor' ||
      run.path !== '.github/workflows/lightweight-monitor.yml' || run.run_attempt !== 1 ||
      run.status !== 'completed' || !['success','failure','cancelled'].includes(run.conclusion) ||
      !['schedule','workflow_dispatch'].includes(run.event) ||
      run.head_sha !== a.workflow_run.head_sha || !/^[a-f0-9]{40}$/.test(run.head_sha || '')) fail('untrusted_checkpoint');
  const jobsResult = await api(env, `runs/${run.id}/jobs?filter=all&per_page=100`);
  const jobs = collection(jobsResult, 'jobs');
  if (jobsResult.total_count !== jobs.length || jobs.some(j => j.status !== 'completed')) fail('incomplete_jobs');
  const monitors = jobs.filter(j => j.name === 'monitor');
  if (monitors.length !== 1 || !Array.isArray(monitors[0].steps)) fail('incomplete_jobs');
  const steps = monitors[0].steps;
  for (const name of ['Bind durable checkpoint to monitor database', 'Run actions/upload-artifact@v4']) {
    const matches = steps.filter(s => s.name === name);
    if (matches.length !== 1 || matches[0].status !== 'completed' || matches[0].conclusion !== 'success') fail('checkpoint_not_bound');
  }
  const bound = stamp(steps.find(s => s.name === 'Bind durable checkpoint to monitor database').completed_at);
  if (!Number.isFinite(bound) || bound > stamp(a.created_at) || bound > now) fail('invalid_checkpoint');
  return {checkpoint_run: run.id, checkpoint_artifact: a.id, producer_conclusion: run.conclusion,
    checkpoint_at: new Date(bound).toISOString(),
    checkpoint_age_seconds: Math.floor((now - bound) / 1000), proof: 'github_bound_checkpoint_and_artifact_digest'};
}

export class MonitorHeartbeat {
  constructor(storage, env) { this.storage = storage; this.env = env; this.running = null; }
  async due(now = Date.now()) {
    const old = await this.storage.get('monitor_heartbeat') || {};
    return !(old.next_check_at > now);
  }
  async check(now = Date.now()) {
    if (this.running) return this.running;
    this.running = this.inspect(now);
    try { return await this.running; } finally { this.running = null; }
  }
  async inspect(now) {
    const old = await this.storage.get('monitor_heartbeat') || {};
    if (old.next_check_at > now) return old;
    const health = {checked_at: new Date(now).toISOString(), next_check_at: now + CHECK_INTERVAL};
    try {
      if (!this.env.GITHUB_TOKEN) fail('token_missing');
      if (this.env.GITHUB_REPO !== REPO || (this.env.GITHUB_REF || 'main') !== 'main' ||
          (this.env.WORKFLOW_FILE || WORKFLOW) !== WORKFLOW) fail('configuration_mismatch');
      Object.assign(health, await checkpoint(this.env, now));
      if (health.checkpoint_age_seconds < 900) health.status = health.producer_conclusion === 'success' ? 'FRESH' : 'CHECKPOINT_ONLY';
      else if (health.checkpoint_age_seconds > 2700) fail('outside_producer_restore_window');
      else if (health.checkpoint_age_seconds > 1800) fail('outside_automatic_dispatch_window');
      else {
        const owners = [];
        for (const status of ACTIVE) {
          const result = await api(this.env, `workflows/${WORKFLOW}/runs?branch=main&status=${status}&per_page=100`);
          const rows = collection(result, 'workflow_runs');
          if (rows.length !== result.total_count || rows.some(r => r.head_branch !== 'main' || r.status !== status ||
              r.head_repository?.full_name !== REPO)) fail('ambiguous_owner');
          for (const row of rows) {
            const created = stamp(row.created_at);
            if (!Number.isSafeInteger(row.id) || !Number.isFinite(created) || created > now) fail('ambiguous_owner');
            owners.push({run_id: row.id, status, age_seconds: Math.floor((now-created)/1000)});
          }
        }
        const last = await this.storage.get(DISPATCH_KEY);
        const reservation = await this.storage.get(RESERVATION_KEY);
        if (reservation !== undefined && (!Number.isSafeInteger(reservation.artifact_id) ||
            !Number.isFinite(reservation.attempted_at) || reservation.attempted_at > now)) fail('invalid_reservation');
        if (owners.length) {
          owners.sort((a,b)=>b.age_seconds-a.age_seconds || a.run_id-b.run_id);
          health.active_owner = owners[0];
          health.active_owner_count = owners.length;
          health.status = owners[0].age_seconds >= 1200 ? 'OVERDUE_OWNER' : 'ACTIVE_MONITOR';
          if (health.status === 'OVERDUE_OWNER') {
            health.category = 'monitor_owner_overdue';
            health.action_required = 'inspect_github_owner_without_duplicate_dispatch';
          }
        }
        else if (last !== undefined && (!Number.isFinite(last) || last > now)) fail('invalid_throttle');
        else if (last !== undefined && now - last < THROTTLE) health.status = 'THROTTLED';
        else if (reservation?.artifact_id === health.checkpoint_artifact) {
          health.status = 'DISPATCH_UNCONFIRMED';
          health.action_required = 'inspect_missing_or_failed_producer_without_duplicate_dispatch';
        }
        else {
          // Reserve durably BEFORE network I/O. An ambiguous POST cannot be retried immediately.
          // One request per checkpoint survives lost responses and owner-list lag.
          await this.storage.put(RESERVATION_KEY, {artifact_id:health.checkpoint_artifact,attempted_at:now});
          await this.storage.put(DISPATCH_KEY, now);
          await api(this.env, `workflows/${WORKFLOW}/dispatches`, {ref: 'main', inputs: {priority_only: 'true'}});
          health.status = 'DISPATCH_ACCEPTED_UNVERIFIED';
        }
      }
    } catch (error) {
      health.status = 'BLOCKED';
      health.category = error instanceof EvidenceError ? error.message : 'unexpected_validation_error';
      health.next_check_at = now + THROTTLE;
    }
    await this.storage.put('monitor_heartbeat', health);
    return health;
  }
}
