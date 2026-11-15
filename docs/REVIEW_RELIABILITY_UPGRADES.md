# Reliability and domain upgrades

Implementation based on main `26d1cbf5d3d705d13d51866349c5322cad33ad7b`.

## Runtime behavior

- Registry comparison baselines advance after all idempotent change writes. A partial write can be retried without losing the remaining differences.
- Missing required trial status and unknown search envelopes are rejected. Explicit zero-result envelopes remain supported.
- CT.gov seed and study polling share a 120-second default budget. Exhausted study polling reports partial coverage.
- FDA cross-feed identity uses a canonical URL and recognizes existing URL identities without rewriting history.
- Local mutations require a local Host, a matching browser Origin when supplied, and JSON content type. Binding the application publicly is not an authenticated hosting solution.
- Optional AI output remains a review draft; the deterministic explanation stays authoritative.

## Domain additions

`live_monitor` now records endpoint, trial-arm, intervention/dose and eligibility changes. Each diff retains the previous and current values, current source hash and previous source hash. Registry evidence remains investigation-only.

`program_identity.register` accepts an explicit stable program ID, alias, ticker, developer/licensee/partner relationship, validity interval, HTTPS evidence URL and observation timestamp. `resolve` returns all supported relationships at an as-of date, excluding evidence observed later. `clinical_payload` exposes matching relationships. Legacy aliases are not automatically interpreted as ownership or synonym evidence; the registry starts empty until evidence-qualified records are registered. Registration is an explicit database write; payload construction never creates this schema.

`event_coverage` is a read-only payload section linking each live event to its configured issuer source health. Rows needing collection lead the list. Event cards expose source coverage separately from event verification. Freshness reflects the published snapshot and is explicitly labeled as such.

## Official newsroom recovery

Reviewed indexes:

- EXEL: https://ir.exelixis.com/press-releases
- INCY: https://incytecorp.gcs-web.com/press-releases
- RCKT: https://ir.rocketpharma.com/news-releases
- VRTX: https://news.vrtx.com/press-releases

All four returned HTTP 200 and recognized same-host release entries during local verification on November 14, 2026. This does not establish GitHub-hosted egress or production source health.

The first index read establishes a baseline without alerts. Subsequent new URLs require a timezone-qualified article `datePublished`; dates older than 48 hours are recorded as seen without fresh alerts. Unknown/future dates remain unresolved, rotate through a three-article budget and report partial coverage. An unrecognized or empty index fails closed. No historical source or delivery ledger is rewritten.

## Release boundary

These changes do not edit the restart authorization, workflow gates, Cloudflare deployment, ACK semantics or OOS gates. Production activation still requires a separately approved exact main SHA under the existing restart policy. A merge by itself must not be described as activation.

Validation commands use Python 3.11+ and Node 24. Install repository dependencies with `pip install -e ".[dev]"` and `npm ci --prefix cloudflare`. Worker runtime tests launch the pinned Wrangler JavaScript entrypoint through Node, including on Windows.
