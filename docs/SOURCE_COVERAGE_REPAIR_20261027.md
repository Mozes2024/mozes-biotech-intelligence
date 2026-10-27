# Source coverage repair and external review disposition

## Observed failure and boundary

Inspected main `c6581f8e078080bdc41385ab8641ee9008b1028f` and the public Pages
snapshot generated `2026-10-27T14:06:37+02:00`, checkpoint
`2026-10-27T14:04:40+02:00`. The snapshot contains 27 active-source FAILED,
eight PARTIAL and 19 partial alternate-coverage labels. All 27 active failures
are HTTP 403 from official company sources. Eight PARTIAL observations mean
`no_rss_found`; they are not ClinicalTrials.gov pagination truncation.

Publication freshness and source success are separate. Re-exporting a checkpoint
cannot restore publisher access. Outside-configuration observations are historical
evidence, not active failures. No `sec_latest:*` observations appear in this
snapshot; this alone does not establish that all other SEC/Edge collection stopped.

The backlog is 58 enrichment pending, 57 manual review, zero eligible, one analysis
pending and one analysis blocked. These counts cannot establish live progress.

## Verified alternate sources and implementation

- TYRA: its SEC annual report identifies `https://tyrabio.investorroom.com` as an
  official investor-relations website:
  <https://www.sec.gov/Archives/edgar/data/1863127/000119312526085490/tyra-20251231.htm>.
  The public news index links its RSS help page, which links
  `https://tyrabio.investorroom.com/news-releases?pagetemplate=rss`.
  This feed returned HTTP 200, valid RSS and five dated same-host releases on
  October 27. The newest was September 14: successful retrieval does not imply
  a new release today. Original `ir.tyra.bio` configuration is preserved.
- CYTK: the existing GlobeNewswire keyword transport returned issuer-authored
  releases with contributor `Cytokinetics, Incorporated`.
- TVTX: the same transport returned issuer-authored releases with contributor
  `Travere Therapeutics, Inc.`.
- Probed 16 missing-alternate candidates. Remaining candidate feeds were empty,
  third-party mentions, or joint-source mentions insufficient to establish an
  issuer's own operational release feed. They were not added to the registry.

New mirror activation is independently forward-only. Its initial contents are
suppressed even with an existing primary-IR baseline. Later releases remain
investigation-only, retain their source/date provenance and are deduplicated.
Wire activation now also excludes equal-boundary and future timestamps. HTML
challenge pages cannot become successful wire RSS scans.

Original FAILED/PARTIAL status, failed-source counts and error history remain
visible. Alternates carry separate timestamps and partial-coverage labels.
Frontend coverage labels expire against the reader's current clock; stale
alternate receipts cannot stay counted indefinitely in an open tab.

The expandable source table shows all active sources, primary cause, last check,
last success, consecutive failures, alternate result/time and safe source links.
Out-of-scope history has its own disclosure. Pending/manual/eligible/blocked
backlog states are shown separately. No live throughput is inferred.

There is no schema migration, dependency addition, score change, validation-gate
change, schedule change, Worker deployment, checkpoint reset or historical replay.
The only workflow addition is an opt-in read-only diagnostic, using a disposable
database with no production-state or delivery credentials.

## Disposition of the supplied GROK review

| Area | Finding against current code | Disposition |
| --- | --- | --- |
| Modularity and typing | `cli.py` is 474 lines; repository-wide typing/refactoring is separate from the source-access failure. | Defer a measured module/import inventory and staged typing plan; no broad refactor in this repair. |
| Coverage and observability | Expandable freshness details, source timestamps, request metrics and scope separation already exist in `web/checkpoint_health.js`, `mozes/payload_v3.py`, `mozes/source_observability.py`. | Improve the per-source UI and actual alternate collection here. CT.gov truncation is a separate coverage limitation. |
| Scores and calibration | `docs/SOURCE_POLICY.md` already calls evidence scores uncalibrated; base rates are not per-event probabilities. | Keep scoring/gates unchanged. Ablation and calibration need a separate experimental contract and real prospective evidence. |
| Frontend | Expandable banner and loading/error handling already exist. A framework rewrite has no demonstrated relation to HTTP 403. | Add accessible table headers, independent alternate states and escaping/unsafe-link regressions. Keep the existing SPA architecture. |
| Testing and OOS | Dev extra already exists. Prospective capture/pilot modules and authorization gates already exist, disabled by default. | Add focused transport/activation/freshness/UI regressions. Do not fabricate a prospective dataset or activate collection/gates. Coverage reporting/property tests remain separate work. |
| Operations | SEC cache/rate limits, bounded retries, source cooldown helpers and online backups already exist. Workflow roles preserve a single authoritative writer. | Do not consolidate workflows or activate Deep Monitor as incidental cleanup. Durable IR-specific denial cooldown remains a possible follow-up, with explicit retry freshness semantics. |
| SQLite WAL | Checkpoint lineage copies a single database file; `tests/test_source_health_scope.py` explicitly requires non-WAL mode. | Reject the unconditional WAL recommendation. A WAL migration needs a compatible backup/checkpoint protocol first. |
| Dependencies | `dependencies=[]` and `[project.optional-dependencies].dev` already exist. | Keep stdlib runtime. No httpx/pydantic/async conversion without measured need. |
| Security and static mode | Existing escaping, source-host validation and read-only consumer contracts are present. | Preserve them and test new source metadata/links against injection. No permission, secret or notification changes. |

## Verification and remaining operational work

Run focused Python tests for official mirrors, wire fallback, source health and TYRA;
run frontend source-health and full dashboard composition regressions; compile touched
Python/JavaScript; inspect the final diff. `scripts/probe_ir_coverage.py` can validate
the three changed issuers on the runner's network without production state.

Local Windows checks need a writable pytest base directory. The repository also
has Linux-oriented tests invoking `python3` and local Wrangler; report these
separately from changed-source regressions and validate them in Ubuntu CI.

Local results on the review candidate: 44 focused source regressions and 119
frontend regressions pass; Python compilation, JavaScript syntax and diff checks
pass. The first broad Windows run had 1,728 passes and three platform failures:
two subprocess-output tests pass when rerun with `PYTHONUTF8=1`; the Stage A
fairness test invokes unavailable `python3`. The separate local workerd runtime
test also requires that Linux-oriented toolchain. Ubuntu CI is the corresponding
release check; neither environment failure is reported as a pass.

The isolated runner probe at
<https://github.com/Mozes2024/mozes-biotech-intelligence/actions/runs/40504199461>
observed active alternate feeds for TYRA, CYTK and TVTX at
`2026-10-27T12:30:03.747705+00:00`, with zero historical signals for all three.
TYRA's original IR source still returned HTTP 403; CYTK/TVTX original sources
still returned `no_rss_found`. This is runner-network evidence, not a production
activation/ACK receipt.

This repair adds alternate collection for three issuers; it does **not** remove
the 27 publisher refusals or provide comprehensive coverage of the full watch
universe. If all three alternates pass on the producer's network, partial backup
coverage can increase from 19 to 22 of the 35 degraded sources, leaving 13 without
an issuer-specific alternate in that snapshot. That is an expected bound, not a
post-deployment measurement or a full-coverage claim.

After review, merging is the production-affecting boundary: ordinary monitoring
must establish new baselines, publish the authoritative artifact, receive real
ACKs and expose the alternate receipts in Pages. Do not dispatch a production
scan, alter timers, replay events, clear queues or synthesize success to test it.
Remaining blocked publishers need a permitted official/publisher source or
publisher-supported access; replacing their FAILED labels would conceal the gap.
