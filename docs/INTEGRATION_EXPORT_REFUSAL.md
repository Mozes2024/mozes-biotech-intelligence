# Integration correction: public refusal must not stale the monitor manifest

Observed on local integration of PRs #39–#44, especially #40's new public budgets.
No original PR branch is changed. This correction is independently based on
production main `c0aa1af489efbf01ea79e194621530f3c04d90ec` and is required before
or with #40, not after a production activation of that presentation.

## Reproduction

With one synthetic pending claim and the review byte limit reduced to 1, the real
reader raises `regulatory_review_public_byte_budget` after the offline simulated
source runner has saved its observation. `pipeline_v2c.main` propagates the error
before creating this run's manifest; a restored manifest still has the previous
source SHA/timestamp. The workflow's always checkpoint/upload steps can bind new
DB bytes to that old manifest. Monitor/Pages restore validate manifest time and
source provenance; a stale manifest is not a reliable new lineage record.

The public guard is correct: no partial list or oversized public output should
be published. The defect is coupling presentation refusal to private observation
checkpoint metadata. Four regression cases fail before correction (both budget
reasons with complete and partial source passes); the unrelated-error case passes.

## Minimal correction

Catch only the two named regulatory presentation budget refusals after monitor
observation. Write a fresh private manifest with actual source runtime status,
current code SHA/time, data_publishable=false and should_publish=false. Preserve
the existing private result protocol and explicit false publication output. Do
not update publication request/acknowledgment state or successful fingerprint.

No public output is created/overwritten by this branch. Full queue evidence and
new observations remain in the existing database for the unchanged serialized
checkpoint/upload mechanism. The manifest reports export refusal explicitly.
Pages validation deliberately refuses data_publishable=false; budget recovery
requires separately reviewed handling, not rewind, row deletion or partial export.

Unrelated export failures still propagate. Plain/offline and consumer-only export
continue to fail closed as before. This correction does not bypass the budget or
create a new writer/cron/flag, change source scans, ACK/delivery policy, B1, A5 or
any schema. Existing frozen public files, historical data and receipts are untouched.
The original six PRs remain unchanged and independent.
