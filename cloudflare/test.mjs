// node cloudflare/test.mjs — cron re-arms the Durable Object; it never dispatches GitHub.
import assert from "node:assert/strict";
import worker from "./src/index.js";

const calls = [];
const env = { HOT_EDGE: { idFromName: name => name,
  get: id => ({ fetch: async url => { calls.push({ id, url }); return new Response("ok"); } }) } };
let work;
await worker.scheduled({ scheduledTime: Date.now() }, env, { waitUntil: promise => { work = promise; } });
await work;
assert.deepEqual(calls, [{ id: "global", url: "https://edge.internal/rearm" }]);
console.log("cloudflare watchdog: all checks passed");
