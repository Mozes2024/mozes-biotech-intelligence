# Bounded discovery fairness and truthful freshness

The existing clinical verification selector could fill its 25-slot budget with
the 40 manually configured priorities before reaching any ordinary discovery.
It now reserves one fifth of that same budget for oldest non-priority attempts,
using durable `last_attempt` ordering. Pending material work is promoted before
the remaining hot candidates. No issuer is removed from priority JSON, no
request budget increases, and SEC cooldown, identity and eligibility checks are
unchanged. This is scheduling priority only, not investment evidence.

The regression uses all forty hot issuers plus six ordinary names, checks the
25-slot bound, and advances attempts to prove the previously unselected issuer
receives a later slot. Source-to-Stage-1 non-priority ingestion is covered by the
separate TYRA PR. This selector fix does not itself activate deep discovery.

Deep-monitor remains suspended. An unconditional false job condition now stops
manual dispatch before checkout, restore or writing the shared artifact. The
old definition remains for a separately reviewed redesign. Comprehensive deep
discovery is **not restored**: isolated discovery plus a primary-writer import,
or another proven single-writer design, still needs implementation, provenance,
serialization and rollout evidence before scheduling can be enabled.

The always-visible freshness banner now separates successful source scan,
durable producer checkpoint and creation time of the published image. A recent
checkpoint or Pages export cannot mask stale/unknown source times or failed
sources. Unknown/future times fail closed. IR coverage is explicitly labeled
as IR; missing IR observations are not asserted to mean missing SEC scans.
Backlog values are labeled as belonging to the loaded snapshot, not live receipts.
Detailed source failures remain available in the existing health section.

The source timestamp is the newest successful individual source, not proof
every source succeeded. A generated timestamp is not a deployment receipt.
No green CI, build or Edge heartbeat is treated as healthy scheduled monitoring.
Three genuine scheduled producer/ACK/publication cycles remain required.
