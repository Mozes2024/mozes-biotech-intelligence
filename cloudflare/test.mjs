// node cloudflare/test.mjs — offline checks with a mocked fetch.
import assert from "node:assert/strict";
import { tick } from "./src/index.js";

const env = { GITHUB_REPO: "o/r", GITHUB_TOKEN: "t", NTFY_URL: "https://ntfy.example/x" };

function mock(runs, dispatchStatus = 204) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, method: init.method || "GET", body: init.body });
    if (url.includes("/runs?")) return new Response(JSON.stringify({ workflow_runs: runs }), { status: 200 });
    if (url.includes("/dispatches")) return new Response(null, { status: dispatchStatus });
    return new Response("ok", { status: 200 });
  };
  return calls;
}

const fresh = new Date("2026-10-06T16:00:30Z");
const recentSuccess = { status: "completed", conclusion: "success", updated_at: "2026-10-06T15:58:00Z" };

let calls = mock([recentSuccess]);
assert.deepEqual(await tick(env, new Date("2026-10-06T16:01:00Z")), { action: "dispatched", status: 204 });
assert.equal(JSON.parse(calls[1].body).ref, "main");

calls = mock([{ status: "in_progress" }, recentSuccess]);
assert.equal((await tick(env, new Date("2026-10-06T16:01:00Z"))).action, "skipped_active");
assert.equal(calls.length, 1);

calls = mock([recentSuccess]);
assert.equal((await tick(env, fresh)).stale_alert, undefined);

calls = mock([{ status: "completed", conclusion: "failure", updated_at: "2026-10-06T15:50:00Z" },
              { status: "completed", conclusion: "success", updated_at: "2026-10-06T14:00:00Z" }]);
const stale = await tick(env, fresh);
assert.equal(stale.stale_alert, true);
assert.ok(calls.some((c) => c.url === env.NTFY_URL));

mock([], 404);
assert.deepEqual(await tick(env, new Date("2026-10-06T16:01:00Z")), { action: "error", status: 404 });

console.log("cloudflare clock: all checks passed");
