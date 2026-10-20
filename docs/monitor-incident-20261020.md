# October 20 monitor incident — verification preparation

No production recovery or notification replay is authorized by this document.
The 45-minute restore gate, incident claim and prospective controls are unchanged.

## Preserved checkpoint

Producer 39310031856, attempt 1, succeeded on main at code
`2569ad87e18d66d162ccc9e22ef970800e63d65e`.
Artifact 12215537669 was created October 19 at 19:23:53 UTC and expires
October 22 at 19:23:49 UTC. Recheck retention and newest producer before use.

The complete ZIP and extracted checkpoint are preserved outside Git in a local
task-private evidence directory with inheritance removed and access granted to
the operator and SYSTEM. This is an independently hash-verifiable local copy,
not a claim of WORM storage or an off-machine backup.

- ZIP SHA256: `393c748b2476ec90976b5db8bd66e165d33bb451d58e24e51444675f3ccba7e8`
- SQLite SHA256: `4dc73cc369e79c63c9009c35a494038de8f82ea79fcf716aab5a076c9e162e1d`
- Checkpoint: October 19 19:23:49 UTC; manifest source scan: 19:23:48 UTC.
- SQLite integrity: ok; foreign key violations: zero.
- Changes: 1,879; outbox: 95 (93 sent, 2 dead); delivery rows: 93;
  Edge links: 46; source archives: 208. No nonterminal outbox rows.

## Observed boundary

Bounded Actions discovery from October 19 19:00 UTC identifies 39328722003 as
the first later monitor execution. It was created October 19 21:35:44 UTC and
failed restore with `stale checkpoint requires reviewed forward recovery and
explicit SHA256`. There was no monitor dispatch in the intervening 136m08s
since the last successful run's creation. This proves the immediate age-gate
failure; it does not prove GitHub's internal reason for missing schedules or
the reason Edge did not dispatch in that interval.

Later scheduled runs also fail; repeated groups of three Worker dispatches
are observed. The Worker has a three-event in-flight budget: timestamps alone
are not evidence that the same event was duplicated. Private event correlation
and complete producer/job audit remain required.

Public read-only observations on October 20 around 07:37 UTC show Edge source
polling continues, with a partial SEC source failure. `/alerts` remains generated
at October 19 19:23:54 UTC. Deployed Pages `updates.json` points to artifact
12215537669 and its exact inner DB hash, published October 19 20:10:01 UTC.
A working Edge heartbeat or a successful Pages build does not prove Stage-1
monitor health.

TYRA has a retained SEC-v2C-equity mapping (CIK 1863127) and active Phase 1/2
candidate NCT05544552, but no TYRA change ledger row in this checkpoint.
Absence from priority JSON does not mean absence from the SEC universe.

## Next gate

The verifier is repinned to these exact reviewed bytes. Its isolated branch
is allowed only for the read-only workflow; it shares the monitor lock and has
no upload, processing, ACK, publication or delivery step. A successful
`VERIFIED_NO_WRITES` is evidence, not authorization to resume processing.
Failure, incomplete inventory, private receipt mismatch or newer writes is NO-GO.
Follow `monitor-lineage-recovery.md` for separately approved forward recovery.
Never reuse a previous incident claim or rerun an old producer.

Liveness is not fixed by repinning this verifier. Three genuine scheduled
checkpoint/collection/ACK/publication cycles remain an acceptance requirement.
