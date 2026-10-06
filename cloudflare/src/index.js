// Free-tier clock for the hot lane. GitHub's own `schedule` trigger drifts by hours, so this
// Worker dispatches the monitor workflow every minute unless a run is already queued/running.
// Free plan budget: 10 ms CPU and 50 subrequests per invocation — this uses 2–3 fetches.

const ACTIVE = new Set(["queued", "in_progress", "waiting", "requested", "pending"]);
const STALE_MINUTES = 45;

function gh(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPO}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "mozes-hot-clock",
      ...(init.headers || {}),
    },
  });
}

export async function tick(env, now = new Date()) {
  const workflow = env.WORKFLOW_FILE || "lightweight-monitor.yml";
  const listed = await gh(env, `/actions/workflows/${workflow}/runs?per_page=10`);
  if (!listed.ok) return { action: "error", status: listed.status };
  const runs = (await listed.json()).workflow_runs || [];

  const result = { action: "skipped_active" };
  if (!runs.some((run) => ACTIVE.has(run.status))) {
    const dispatched = await gh(env, `/actions/workflows/${workflow}/dispatches`, {
      method: "POST",
      body: JSON.stringify({ ref: env.GITHUB_REF || "main", inputs: { priority_only: true } }),
    });
    result.action = dispatched.status === 204 ? "dispatched" : "error";
    result.status = dispatched.status;
  }

  // Dead-man's switch: once per half hour, warn if nothing has succeeded recently.
  if (env.NTFY_URL && now.getUTCMinutes() % 30 === 0) {
    const lastSuccess = runs.find((run) => run.conclusion === "success");
    const ageMinutes = lastSuccess ? (now - new Date(lastSuccess.updated_at)) / 60000 : Infinity;
    if (ageMinutes > STALE_MINUTES) {
      await fetch(env.NTFY_URL, {
        method: "POST",
        headers: { Title: "MOZES monitor is stale", Priority: "4", Tags: "warning" },
        body: `No successful monitor run for ${Number.isFinite(ageMinutes) ? Math.round(ageMinutes) + " min" : "a long time"}.`,
      });
      result.stale_alert = true;
    }
  }
  return result;
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(tick(env, new Date(controller.scheduledTime)).then((r) => console.log(JSON.stringify(r))));
  },
  async fetch() {
    return new Response(JSON.stringify({ ok: true, service: "mozes-hot-clock" }), {
      headers: { "content-type": "application/json" },
    });
  },
};
