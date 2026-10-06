# Hot-lane deployment (free)

Everything runs on free tiers; no server, no card.

| Piece | Free tier | Role |
|---|---|---|
| Cloudflare Worker `mozes-hot-clock` (`cloudflare/`) | Workers Free: cron every minute | Clock: dispatches `lightweight-monitor.yml` every minute unless a run is active; warns via ntfy if no run succeeded for 45 min |
| GitHub Actions `lightweight-monitor.yml` | Public repo: unlimited minutes | Compute: FDA / wires / Nasdaq halts / company IR / priority SEC → outbox → push. Single push owner (`MOZES_PUSH_FROM_ACTIONS=1`) |
| ntfy.sh | Free | Phone push |
| GitHub Pages | Free | Site, rebuilt at most every 15 min |

GitHub's own `schedule` drifts by hours, so it remains only as a fallback. Vercel Hobby cron is
once a day, so it can't act as the clock.

The workflow is one serialized lineage (`concurrency: mozes-hot-monitor`, never cancelled), and each
run restores the previous run's DB, so the outbox stays exactly-once. The first run of the hot
step on any DB is a silent warm-up: backlog becomes the baseline instead of a burst of pushes.

## One-time setup

1. **ntfy**: install the ntfy app, subscribe to a long random topic (e.g. `mozes-7f3k9q2x`).
2. **Repo secret**:
   ```bash
   gh secret set MOZES_NTFY_URL --body "https://ntfy.sh/<topic>"
   ```
3. **GitHub token for the Worker**: GitHub → Settings → Developer settings → Fine-grained tokens →
   *Only select repositories*: `mozes-biotech-intelligence` → Repository permissions → **Actions: Read and write**.
4. **Cloudflare** (free account, no card):
   ```bash
   cd cloudflare
   npx wrangler login
   npx wrangler secret put GITHUB_TOKEN     # paste the token from step 3
   npx wrangler secret put NTFY_URL         # optional: same ntfy URL, for the "monitor stale" warning
   npx wrangler deploy
   ```
5. Check: `npx wrangler tail` shows `dispatched` / `busy` every minute, and
   `gh run list --workflow lightweight-monitor.yml` shows runs about one minute apart.

To stop the minute clock: `npx wrangler delete` (the GitHub fallback crons keep running).

## Latency

A push arrives about 1–2 minutes after the source publishes: up to 1 min waiting for the tick, then
roughly 1 min of runner start-up and the pass (feeds are fetched in parallel).

## Optional: always-on server instead

If a free or owned always-on machine is available, `python -m mozes.hot_monitor --interval 60`
does the same loop in-process (heartbeat, backups). Use **either** that **or** the Actions push owner,
never both: remove `MOZES_NTFY_URL` from the repo secrets before starting a server worker.

- Docker: `docker build -t mozes-hot . && docker run -d --restart unless-stopped --env-file deploy/hot.env -v mozes-data:/data mozes-hot`
- systemd: copy `deploy/mozes-hot.service`, put env in `/etc/mozes/hot.env` (see `deploy/hot.env.example`).
