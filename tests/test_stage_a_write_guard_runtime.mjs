// Execute the exact pinned writeguard with real workerd and D1.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, execFileSync } from 'node:child_process';

const repoRoot = new URL('..', import.meta.url).pathname;
const buildRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'issuer-write-'));
execFileSync('python3', [path.join(repoRoot, 'scripts', 'build_stage_a_write_guard.py'),
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

const PORT = 18730 + Math.floor(Math.random() * 200);
const BASE = `http://127.0.0.1:${PORT}`;
// EDGE_FORWARD_ONLY_FROM is a dashboard-set production var (keep_vars=true),
// required by the forward-only fence in /edge/health; seeded here for local dev.
const dev = spawn(wcmd, wargs(['dev', '--local', '--port', String(PORT), '--inspector-port', '0', '--config', 'wrangler.toml',
  '--var', 'EDGE_FORWARD_ONLY_FROM:2026-10-21T00:00:00Z', '--var', 'EDGE_SYNC_TOKEN:test-sync']),
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


const query = (sql) => {
  const raw=d1(sql).toString();
  const value=JSON.parse(raw.slice(raw.indexOf('[')));
  return value[0].results;
};
const row=(cik,ticker,company)=>({cik,ticker,company,confidence:.99,source:'SEC-v2C-equity'});
const a=row('123','AAA','Alpha Bio'),b=row('456','BBB','Beta Bio');
const sync=async(issuers)=>{
  const r=await fetch(`${BASE}/edge/sync`,{method:'POST',headers:{Authorization:'Bearer test-sync','Content-Type':'application/json'},body:JSON.stringify({issuers})});
  assert.equal(r.status,200);assert.deepEqual(await r.json(),{ok:true,count:issuers.length});
};
test('pinned issuer sync writes only changes, keeps atomic rollback and auth', {timeout:120000}, async()=>{
  d1("CREATE TABLE issuer_audit(op TEXT); CREATE TRIGGER issuer_insert AFTER INSERT ON edge_issuers BEGIN INSERT INTO issuer_audit VALUES('insert'); END; CREATE TRIGGER issuer_update AFTER UPDATE ON edge_issuers BEGIN INSERT INTO issuer_audit VALUES('update'); END;");
  await sync([a,b]);
  assert.equal(query('SELECT count(*) n FROM issuer_audit')[0].n,2);
  const initial=query('SELECT * FROM edge_issuers ORDER BY cik');
  await sync([a,b]);
  assert.equal(query('SELECT count(*) n FROM issuer_audit')[0].n,2);
  assert.deepEqual(query('SELECT * FROM edge_issuers ORDER BY cik'),initial);
  await sync([{...a,company:'Changed Bio'},b]);
  assert.equal(query('SELECT count(*) n FROM issuer_audit')[0].n,3);
  assert.equal(query("SELECT company FROM edge_issuers WHERE cik='123'")[0].company,'Changed Bio');
  await sync([a]);
  assert.equal(query("SELECT active FROM edge_issuers WHERE cik='456'")[0].active,0);
  const inactive=query("SELECT * FROM edge_issuers WHERE cik='456'")[0];
  await sync([a]);
  assert.deepEqual(query("SELECT * FROM edge_issuers WHERE cik='456'")[0],inactive);
  await sync([a,b]);
  assert.equal(query("SELECT active FROM edge_issuers WHERE cik='456'")[0].active,1);
  const before=query('SELECT * FROM edge_issuers ORDER BY cik');
  const count=query('SELECT count(*) n FROM issuer_audit')[0].n;
  d1("CREATE TRIGGER reject_bad BEFORE INSERT ON edge_issuers WHEN NEW.ticker='BAD' BEGIN SELECT RAISE(ABORT,'fixture'); END;");
  const r=await fetch(`${BASE}/edge/sync`,{method:'POST',headers:{Authorization:'Bearer test-sync','Content-Type':'application/json'},body:JSON.stringify({issuers:[{...a,company:'Must rollback'},row('789','BAD','Fail Bio')]})});
  assert.equal(r.status,500);
  assert.deepEqual(query('SELECT * FROM edge_issuers ORDER BY cik'),before);
  assert.equal(query('SELECT count(*) n FROM issuer_audit')[0].n,count);
  const unauthorized=await fetch(`${BASE}/edge/sync`,{method:'POST',body:JSON.stringify({issuers:[a]})});assert.equal(unauthorized.status,401);
  const empty=await fetch(`${BASE}/edge/sync`,{method:'POST',headers:{Authorization:'Bearer test-sync'},body:JSON.stringify({issuers:[]})});assert.equal(empty.status,400);
});
