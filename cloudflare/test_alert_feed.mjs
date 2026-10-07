// Exercise HTTP contracts and real SQLite upsert ordering through the D1 binding shape.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, sep } from "node:path";
import { handleAlertFeed } from "./src/alert-feed.js";

const directory = mkdtempSync(join(tmpdir(), "mozes-feed-"));
const path = join(directory, "alerts.db");
const python = `import sys,json,sqlite3
sys.stdin.reconfigure(encoding='utf-8')
value=json.load(sys.stdin)
conn=sqlite3.connect(value['path']);conn.row_factory=sqlite3.Row
if value.get('script'):conn.executescript(value['script']);out=None
else:
 c=conn.execute(value['sql'],value['params'])
 if value['mode']=='first':
  row=c.fetchone();out=dict(row) if row else None
 else:out={'meta':{'changes':c.rowcount}}
conn.commit();conn.close();print(json.dumps(out))`;
function sql(value) {
  const result = spawnSync(process.env.PYTHON || "python", ["-c", python], {
    input: JSON.stringify({ path, ...value }), encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}
sql({ script: readFileSync(new URL("./migrations/0001_alert_feed.sql", import.meta.url), "utf8") });
const env = { ALERT_FEED_TOKEN: "secret", ALERTS_DB: { prepare(query) {
  let params = [];
  return { bind(...values) { params = values; return this; },
    async run() { return sql({ sql: query, params, mode: "run" }); },
    async first() { return sql({ sql: query, params, mode: "first" }); } };
} } };
const now = new Date("2026-10-07T18:00:00Z");
const feed = { schema: 1, sequence: 10, revision: "a".repeat(64), generated_at: now.toISOString(),
  alerts: [{ change_id: "CHG-123", priority: "P1", polarity: "negative", headline: "Failed endpoint",
    watched: true, source_url: "https://company.example/news", delivered: false,
    delivery: { email: { status: "failed", attempts: 1, last_error: "private-address@example.com" } },
    explanation: {basis:"source_text",method:"source-rules-v1",status:"complete",ai_status:"disabled",
      summary_he:"הניסוי לא עמד במדד הראשי",why_he:"נמצא שינוי מהותי",source_polarity:"negative",
      missing_he:[],evidence:[{quote:"The trial failed its primary endpoint",url:"https://company.example/news",topic:"clinical"}],
      internal_key:"must also stay private"} }],
  alert_config: { enabled_channels: ["email"], feed_url: "https://clock.example/alerts" },
  private_token: "never public" };
function post(value, token = "secret") {
  return new Request("https://clock.example/alerts", { method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(value) });
}
try {
  assert.equal((await handleAlertFeed(new Request("https://clock.example/alerts"), env, now)).status, 503);
  assert.equal((await handleAlertFeed(post(feed, "wrong"), env, now)).status, 401);
  assert.equal((await handleAlertFeed(post(feed), env, now)).status, 200);
  const got = await handleAlertFeed(new Request("https://clock.example/alerts"), env, now);
  assert.equal(got.headers.get("Access-Control-Allow-Origin"), "https://mozes2024.github.io");
  const publicText = await got.text();
  assert(!publicText.includes("never public") && !publicText.includes("private-address"));
  assert(!publicText.includes("must also stay private"));
  assert.equal(JSON.parse(publicText).alerts[0].polarity, "negative");
  assert.equal(JSON.parse(publicText).alerts[0].explanation.source_polarity, "negative");
  assert.equal((await handleAlertFeed(post(feed), env, now)).status, 200); // retry is idempotent
  assert.equal((await handleAlertFeed(post({ ...feed, sequence: 9, generated_at: "2026-10-07T18:01:00Z" }), env, now)).status, 409);
  assert.equal((await handleAlertFeed(post({ ...feed, sequence: 11, revision: "b".repeat(64) }), env, now)).status, 200);
  assert.equal((await handleAlertFeed(post(feed), env, now)).status, 409);
  assert.equal((await handleAlertFeed(post({ ...feed, alerts: [{ ...feed.alerts[0], source_url: "javascript:alert(1)" }] }), env, now)).status, 400);
  assert.equal((await handleAlertFeed(post({ ...feed, private_token: "x".repeat(300_000) }), env, now)).status, 413);
  assert.equal((await handleAlertFeed(post(feed), { ALERT_FEED_TOKEN: "secret" }, now)).status, 503);
  console.log("cloudflare alert feed: all checks passed");
} finally {
  assert(resolve(directory).startsWith(resolve(tmpdir()) + sep + 'mozes-feed-'));
  rmSync(directory, { recursive: true, force: true });
}
