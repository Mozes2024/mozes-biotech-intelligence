# Staged deployed-token proof: requires separate deployment authorization

SUPERSEDED: the separately reviewed one-shot probe failed `network_or_timeout`
and was removed with the prior Worker restored. Do not repeat or redeploy it.
The text below preserves the original proposal, not a current next action.
Current implementation work is tracked in `BIO_IMPLEMENTATION_TRACKER.md`.

Current production has no GET-only token probe. Neither a local GH credential
nor the Cloudflare read token proves the Worker binding's Actions permissions.
Do not retrieve, export, replace or log the existing GITHUB_TOKEN.

Smallest staged proposal: deploy the reviewed Worker with the watchdog in an
explicit GET-only validation mode, before enabling its dispatch path. The same
global DO and token binding would enumerate artifacts, fetch the selected owner's
run and complete jobs, and query all five active workflow states, irrespective of
checkpoint freshness. Fixed repository/workflow, redirect rejection, bounded
requests/response sizes and sanitized HTTP status-only results must remain.
The validation path must have no POST branch and must not return raw payloads,
headers, URLs or secrets. Ordinary event monitoring remains unchanged; this mode
does not authorize any manual monitor run, recovery, credential change or replay.

Require review of the exact minimal mode diff and its offline no-POST, host/path
allowlist, incomplete-evidence and status-sanitization tests; then separate approval
for its staged Worker deployment. One genuine alarm obtains proof. Stop on any
failure. A later separately authorized deployment enables ordinary heartbeat
dispatch only after all GET contracts pass. Retain namespace, DO timestamps and
120-second interval throughout; rollback preserves durable throttles and history.

This document is a proposal only. No validation mode or production deployment is
implemented/activated by this PR, and production Actions READ remains UNVERIFIED.
