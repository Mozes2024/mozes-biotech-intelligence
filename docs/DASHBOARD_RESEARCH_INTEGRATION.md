# Research priority in the effective dashboard

Baseline: main `5a0bd8a0b9c6f6d9eca03c9df8dff2cf17663a95` (merged PR #30).

## Ownership and correction

`index.html` loads checkpoint health, app, alerts, Live Intelligence, financial
context, then coverage. `app.js` supplies the research selector and section.
Live Intelligence replaces `dashboard`; coverage wraps that replacement and
`render`. Live Intelligence, financial context and coverage also wrap event cards.
Testing only app's dashboard therefore missed the production composition.

The active Live Intelligence dashboard now inserts `researchPrioritySection(live)`
after its main shortlist and before changes, clinical catalysts and coverage.
`live` is the complete event population; the main shortlist continues to use its
existing ticker deduplication. A WATCH event and a research event for KOD can both
appear. The existing selector, six-card cap, source/date/PIT/security/risk filters,
missing-information badges and non-investment wording remain unchanged.

Both affected assets use `research-priority-integration-v1`. Checkpoint health
keeps `checkpoint-timezone-v1`; alert assets and script order are unchanged.
No service worker, engine, financial evidence, gates or data format changes.

## Executed regression

`tests/test_dashboard_integration.mjs` parses the actual script references in
`index.html`, executes all files in order (including application startup), and
runs the final dashboard through the real coverage render wrapper. Browser DOM,
network, clock and timers are deterministic doubles; application helpers are real.
The fixture is a documented UI projection of the reviewed frozen post-PR30 payload,
containing all nine WATCH events and the six research events, not fabricated scores.

Before the fix, execution found six eligible events but rendered zero research
sections: five of the six initial integration cases failed. After the fix, all
seven cases pass, including the additional asset-version contract. They cover:

- Exactly one section, six research cards and the original six main WATCH cards.
- Live Intelligence, market warnings, financial/provenance cards, health, clinical
  catalysts, coverage, changes and one compact alerts section.
- Critical/high risk, acquisition, expired windows, insufficient verification,
  future source retrieval and absent primary sources; empty and overflow inputs.
- Unchanged revision/newer offset checkpoint refresh through the final renderer,
  equivalent-instant no-op, independent alert preservation and change_id dedup.
- Missing-information/non-investment wording, no evidence score on research cards,
  no duplicate cards, baseline popups or repeated system notifications.

The isolated Python health-render test adds the new app helper to its existing
helper doubles. The integration suite exercises that helper's real implementation.
CI now runs the integration suite alongside the existing 87 checkpoint/alert tests.
Final local runs: 94 frontend cases and 958 Python cases passed. Complete Worker,
D1, smoke and Docker validation results are reported with exact-head CI in the PR.

## Frozen-data and browser acceptance

Read-only scoring of artifact `11747838323` retains 198 events: 78 insufficient,
107 stale, nine WATCH, two observed-negative AVOID_BINARY, one resolved and one
quarantined. Within the insufficient population, 67 source-backed estimates and
two reconciliation cases remain partial; no heuristic becomes verified runway.
Both OOS gates remain disabled/unsatisfied and forward_candidates remains empty.
Database SHA256 before/after is
`07da9070841fa058f4fd11ece46f9b76d80eeec04b5be6e5ba36e5ca1da89ffb`;
query_only is enabled and total_changes is zero.

A real local browser loaded the complete HTML/script chain with the full frozen
payload. Its DOM displayed six WATCH cards plus these six research cards, all
marked "חסר מידע", with "זו אינה המלצת השקעה":

- KOD-PEAK-PA1
- AUTO-HROW-NCT07832552
- AUTO-MRNA-NCT06033261
- AUTO-ALKS-NCT06843590
- AUTO-BHVN-NCT06309966
- AUTO-CERS-NCT03037164

The frozen checkpoint correctly appears old under the current reader clock.
This is local verification, not a claim of deployment. Monitor/Pages architecture,
Cloudflare, alert delivery/ACK/receipts, history and recovery are untouched.
The earlier producer_busy attempts are independent of this frontend defect.
Merge and live rollout require separate approval. A5 is not started here.
