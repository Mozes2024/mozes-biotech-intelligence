# Query-ID discovery recovery and issuer organization feed

## Production baseline verified

PR #95 is merged as `1f458c4d03f5fe1585cb45f689d42ec9a6629440`. Main CI
40507144132 and automatic Pages deployment 40507720900 passed. Subsequent genuine
producers 40535712393, 40539723262 and 40543704695 and their corresponding Pages
runs passed on that commit. The public snapshot generated
`2026-10-27T18:47:41+02:00` binds producer 40543704695, artifact 12751432363 and
checkpoint `2026-10-27T18:45:41+02:00`. All three TYRA/CYTK/TVTX alternate receipts
are active. There are 22 partial alternates and 13 degraded sources without one.
This is observed production evidence, not the earlier isolated probe prediction.

## Two further causes and the bounded repair

### ALNY query-ID releases

The official page <https://investors.alnylam.com/press-releases> returns HTTP 200
with ten release anchors shaped as `/press-release?id=<UUID>`. The old parser
only examines the path's last component for a numeric ID or long hyphenated slug.
It rejects all ten because `press-release` alone matches neither pattern.

The parser now accepts a single nonempty UUID `id` only on the explicit
`press-release` / `news-release` endpoint. Existing HTTPS/same-host, descriptive
headline and twelve-entry bounds remain. Navigation links, arbitrary query IDs,
multiple/blank IDs and off-host URLs remain ineligible. The registry points at
the official press index, rather than the generic investor landing page.

An HTML discovery receipt is not a dated verified catalyst. Releases remain
`investigation_only` and `headline_only`; no source date, readout date, score or
trading gate is inferred from an index anchor.

### ARGX organization RSS

The keyword feed previously probed for argenx mostly returns mentions by other
publishers. A distinct public organization feed at
<https://rss.globenewswire.com/RssFeed/Organization/2l6VaQppBelbJuj0TwBXvg==>
returns HTTP 200, RSS channel title `argenx SE`, twenty items and contributor
`argenx SE`. It is added as a new issuer fallback with contributor `argenx`.
This uses the existing HTTPS GlobeNewswire allowlist, forward-only activation
and canonical-release deduplication. The original IR status stays visible.

## Upgrade safety

Recovering previously invisible anchors must not replay ten old releases as
today's news if an older HTML baseline happens to exist. HTML baselines now
bind `site` and `parser_version=2`. A missing/version-mismatched baseline or a
changed index URL records a new initial baseline without producing signals.
Previously known URLs are retained within the existing 200-URL bound. Subsequent
new URLs can become investigation signals and repeated polls deduplicate them.

The conservative tradeoff is that the first upgrade pass baselines current
index contents, including any genuinely new item already present then. This
avoids claiming an unknown historical publication time or triggering replay.
No production event, receipt, delivery cursor or historical source is removed.

No schema, dependency, schedule, Worker, score, alert channel or validation-gate
change. The existing opt-in disposable diagnostic covers five reviewed issuers
and now exposes `index_links`, making restored HTML discovery directly visible.

## Verification and remaining scope

Focused tests cover UUID identification, malformed/ambiguous/navigation links,
same-host filtering, headline constraints, the twelve-entry limit, new and legacy
baselines, changed index identity, retention of old URLs, new-item ingestion and
deduplication. Captured public HTTP bodies replay to ten ALNY index links and an
active ARGX alternate, with zero initial signals in both disposable databases.
Runner-network verification and full Ubuntu CI remain required before merging.

Relative to the verified 18:47 snapshot, successful rollout is expected to move
ALNY from PARTIAL to operational `html_fallback` and add partial alternate coverage
for ARGX: 27 FAILED, seven PARTIAL, 23 partial alternates and eleven degraded
sources without a confirmed alternate. Those are expectations until observed
in ordinary producer/artifact/ACK/Pages cycles after review and merge.

Remaining issuers are AMGN, ARWR, CRSP, EXEL, GILD, INCY, NBIX, ONC, RCKT, SLS
and VRTX. The inspected alternate GCS/RSS hosts still returned access denials;
some corporate news pages return JavaScript shells without validated article
anchors or an advertised RSS feed. HTTP 200 alone is not operational coverage.
No unverified feed, third-party mention, mirrored search page, vendor-access
bypass or blanket green status is introduced for these issuers.
