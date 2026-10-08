import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import worker, { syncIssuers } from "./src/index.js";
import { HotEdge, fastOutcome, parseSecAtom } from "./src/hot-edge.js";

const atom = readFileSync(new URL("../tests/fixtures/sec_latest_8k.atom", import.meta.url), "utf8");
const entries = parseSecAtom(atom);
assert.equal(entries[0].cik, "1035422");
assert.equal(entries[0].form, "8-K");
assert.equal(fastOutcome("Company did not meet its primary endpoint").polarity, "negative");
assert.equal(fastOutcome("Company met its primary endpoint").polarity, "positive");
assert.equal(fastOutcome("FDA lifted the clinical hold").polarity, "positive");
assert.equal(fastOutcome("The result was not statistically significant").polarity, "negative");
assert.equal(fastOutcome("8-K filed").material, false);

let alarm = null;
const memory = new Map();
const storage = { getAlarm: async () => alarm, setAlarm: async value => { alarm = value; },
  get: async key => memory.get(key), put: async (key, value) => { memory.set(key, value); } };
const edge = new HotEdge({ storage }, { EDGE_INTERVAL_SECONDS: "1", SEC_USER_AGENT: "example agent" });
const oldFetch = globalThis.fetch;
globalThis.fetch = async () => { throw Error("offline"); };
try {
  await edge.alarm();
  assert.ok(alarm > Date.now());
  assert.ok(alarm - Date.now() >= 9000);
  const health = await (await edge.fetch(new Request("https://edge.internal/health"))).json();
  assert.equal(health.sources.sec_8K?.status, undefined);
  assert.equal(health.sources["sec_8-K"].status, "FAILED");
  assert.equal(health.sources["sec_6-K"].consecutive_errors, 1);
  assert.ok(health.next_alarm_at);
} finally { globalThis.fetch = oldFetch; }

const env = { EDGE_SYNC_TOKEN: "secret" };
assert.equal((await syncIssuers(new Request("https://edge.example/edge/sync", { method: "POST", body: "{}" }), env)).status, 401);
const batches = [];
env.ALERTS_DB = { prepare: sql => ({ bind: (...values) => ({ sql, values }) }),
  batch: async statements => { batches.push(statements); } };
const issuer = { cik: "123", ticker: "ZZZZ", company: "Novel Bio", confidence: .99, source: "SEC-v2C-equity" };
const authorized = new Request("https://edge.example/edge/sync", { method: "POST",
  headers: { Authorization: "Bearer secret" }, body: JSON.stringify({ issuers: [issuer] }) });
assert.equal((await syncIssuers(authorized, env)).status, 200);
assert.equal(batches[0].length, 2);
let rearmed = false;
await worker.scheduled({ scheduledTime: Date.now() }, { HOT_EDGE: {
  idFromName: () => "global", get: () => ({ fetch: async () => { rearmed = true; return new Response(); } }),
} }, { waitUntil: promise => promise });
await new Promise(resolve => setTimeout(resolve, 0));
assert.equal(rearmed, true);
console.log("cloudflare hot edge: all checks passed");
