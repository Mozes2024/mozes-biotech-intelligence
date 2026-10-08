# Alert delivery and source explanations

> This document records the v2H alert-feed deployment. For the v3 detection
> clock, issuer synchronization, Stage-0 delivery, and current activation steps,
> see [HOT_EDGE_V3.md](HOT_EDGE_V3.md). The v2H clock description below is
> historical after v3 activation.

## Review status

The v2H alert path passed its then-current offline Python suite, Worker clock/feed checks and a
local browser popup check before publication. The existing Cloudflare Worker
now has the `ALERTS_DB` binding and `/alerts` endpoint; its scheduled clock remains
active. The public endpoint is `https://mozes-hot-clock.sp500.workers.dev/alerts`.
GitHub has the matching private feed token and public URL configured.

Production email remains disabled until the SMTP sender and App Password are
configured. The approved recipient is stored in the GitHub recipient secret.
No real email or paid model request was sent during development.

## Runtime path

1. The existing Cloudflare clock starts the serialized hot GitHub workflow.
2. Detection records an immutable `change_events` row and queues Stage-1 delivery.
3. SMTP / ntfy / webhook delivery is attempted before source enrichment.
4. Source enrichment processes up to three queued alerts, oldest first. The workflow
   caps this step at one minute. Source failures fall back to RSS summary/headline
   and retry after 5 and 10 minutes; after three attempts the result is unavailable.
5. Immutable normalized source documents and versioned analyses stay in SQLite.
6. The hot owner posts a bounded public snapshot to the existing Worker `/alerts`.
   D1 stores one snapshot and atomically rejects an older hot-workflow run number.
7. The open browser polls the alert endpoint every 30 seconds. Pages remains the
   research dashboard and a fallback alert source, with small `alerts.json` and
   `updates.json` files. The full research JSON is fetched only when its content
   revision changes.

The small feed is capped at 256 KiB. It contains alert text, public provenance,
source excerpts and per-channel status, but no recipient addresses, tokens, SMTP
errors, source bodies or internal diagnostic trees.

## Behavior

- Recovery scans missing eligible changes from the last seven days. Durable enqueue
  decisions prevent already-filtered changes from occupying every recovery batch.
- `delivered` means at least one external channel accepted the message. A log-only
  receipt does not set it. Each channel also has its own pending/sending/sent/failed/
  dead state. SMTP acceptance does not prove inbox arrival.
- SMTP remains at-least-once after an ambiguous crash. A stable Message-ID helps
  identify retries; it does not guarantee provider-side deduplication.
- Source explanations distinguish full text, RSS summary, headline-only and
  structured exchange/registry evidence. They show an exact excerpt and missing
  information. Body/headline classification disagreements are explicitly flagged.
- Rule-based interpretation is uncalibrated. It cannot change event verification,
  scoring, paper history or empirical trading gates. A wire story does not become a
  verified FDA decision because the explanation reader fetched it.
- System notifications are optional. In-page alerts cover positive, negative,
  mixed and unknown polarity without requiring system-notification permission.
  First load establishes a quiet baseline; reload keeps seen IDs. Web Locks plus
  shared local storage coordinate supported browsers. With storage/locks disabled,
  duplicate suppression is limited to the current tab.
- Cloudflare failure falls back to Pages. A fallback snapshot older than the most
  recent received feed cannot replace it. Timing still depends on source polling,
  Actions start/finish time and network availability; there is no sub-second SLA.

## Activation after approval

Use the existing Worker and account. No additional clock or push owner is needed.

1. Create a D1 database for alert snapshots in the authorized Cloudflare account.
   Record its actual ID, uncomment the `ALERTS_DB` binding in `wrangler.toml`, and
   apply `cloudflare/migrations/0001_alert_feed.sql` to that database.
2. Configure the Worker secret `ALERT_FEED_TOKEN` with a random private token. Set
   the same value as GitHub secret `MOZES_ALERT_FEED_TOKEN`. Never put it in public
   variables, source code, the browser or a URL.
3. Deploy the Worker update through the existing deployment process. Check `/`
   still serves the clock health response, unauthorized POST `/alerts` returns 401,
   and GET `/alerts` returns 503 until the first authenticated publish.
4. Set GitHub variable `MOZES_ALERT_FEED_URL` to the observed existing Worker URL
   plus `/alerts`. `SITE_ORIGIN` defaults to `https://mozes2024.github.io`.
5. Review and publish this branch through normal CI/Pages deployment. Confirm the
   next hot run uploads its DB artifact and publishes the alert snapshot. The feed
   publish step cannot turn an already-saved monitor artifact into a failed lineage.
6. Confirm existing `MOZES_SMTP_USER`, `MOZES_SMTP_PASSWORD` and optional
   `MOZES_ALERT_EMAIL_TO` settings through the authorized account. Test actual email
   receipt only after the intended recipient and permission to send are confirmed.
7. AI wording is disabled by default. It can be enabled explicitly using
   `MOZES_ALERT_AI=1`, the existing `MOZES_AI_MODEL` variable and provider key.
   It consumes provider usage and can lengthen enrichment. Outputs require valid
   evidence references and reject numeric facts absent from the excerpt; this is
   not a complete factual-entailment check. Deterministic fallback stays available.

The implementation targets the current single-recipient SMTP setup. Multi-user
subscriptions and preferences require a separate authenticated user/data design.

## Verification

- Python suite: `python -m pytest -q`.
- Worker clock: `node cloudflare/test.mjs`.
- Worker feed HTTP and SQLite ordering: `node cloudflare/test_alert_feed.mjs`.
- Syntax: `python -m compileall -q mozes scripts`; `node --check` for web JS.
- Offline export: `mozes bootstrap-v2`, then `mozes export-v3`; verify all three
  JSON artifacts. Explanations are available in the local `/api/alerts` endpoint.
- Browser: add a new synthetic negative alert while the page is open; confirm its
  popup appears, the source excerpt is readable, repeated polls stay quiet and a
  second tab/reload does not announce the same alert again.

Remote Worker execution, production SMTP delivery and source accessibility from
the runner remain release checks, not claims established by offline tests.

## Rollback

Revert application/Worker code through the existing release process and disable
`MOZES_ALERT_FEED_URL` if necessary. Preserve SQLite alert and source tables and the
D1 snapshot; rollback does not require deleting either. The existing clock and
single SMTP push owner must remain intact.

## Primary interface references

- [D1 prepared statement API](https://developers.cloudflare.com/d1/worker-api/prepared-statements/)
- [D1 setup and bindings](https://developers.cloudflare.com/d1/get-started/)
