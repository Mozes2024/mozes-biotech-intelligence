# BIO evidence recovery after CU deletion

Prepared 2026-11-02. This inventory does not authorize production changes.

## Original audit documents survived in cloud storage

The two original October 11 files were found and read in full. Their reported
byte sizes exactly match the pre-deletion local inventory, but the deleted
files' hashes were never recorded, so byte-for-byte identity cannot be proved.

- [CLAUDE_PASS1_INDEPENDENT_AUDIT.md](https://chatgpt.com/library?file_id=libfile_dd2f7449f43c81918c1b479ffaf65cb1&account_id=dc3cae69-1a7b-4849-b4c8-1f7e87debf91)
  — 30,783 bytes, 305 lines.
- [CLAUDE_PASS2_DELTA_AND_ROADMAP.md](https://chatgpt.com/library?file_id=libfile_4f8c8f2e124881919ea383f4e6a3ba28&account_id=dc3cae69-1a7b-4849-b4c8-1f7e87debf91)
  — 23,223 bytes, 220 lines.

Both describe baseline `527133b98b68cc64a4f72afeabfed0916eff81ae`.
They are historical audit reports, not fresh findings about today's main.
Pass 1 records independent code/runtime observations; Pass 2 reconciles them
with previous reviews and proposes dependency ordering. Examples include
lineage liveness, Pages reader/producer separation, enrichment retry occupancy,
trial linkage, outcome negation and missing-evidence semantics.

Do not automatically implement their recommendations. Some findings have since
been addressed. Reconcile each against the current
[BIO implementation tracker](../../BIO_IMPLEMENTATION_TRACKER.md),
[operations runbook](../../operations-recovery-runbook.md) and
[research roadmap](../../RESEARCH_ROADMAP.md). Preserve all current incident,
lineage, deployment and OOS gates.

## Production source and remaining local losses

The canonical checkout remains at
`C:/workspace/MozesProjects/Repos/mozes-biotech-intelligence`, observed HEAD
`761b9790f207399cace10b78b207d3ff2ad6bfcd`. Read-only
`git fsck --connectivity-only --no-dangling` passed on November 2.

Before deletion, the CU checkout had the same HEAD and no tracked modifications;
`review-output/` was the only normal untracked status entry. However, ignored
local files do not appear in that status and must not be assumed preserved.

A pre-deletion content comparison found 36 absent files and 6 differing files
outside Git metadata, Python bytecode and pytest cache. This was a byte-level
comparison; line endings and generated files can contribute differences.
The complete comparison list/hashes were not retained. Known absent examples:

- `docs/EMAIL_SETUP_HE.md`, `docs/TELEGRAM_SETUP_HE.md`
- `mozes/email_setup.py`, `mozes/telegram_setup.py`
- `scripts/push_telegram_secrets.py`
- the two recovered audit reports
- generated package metadata and files under ignored `_reviews/`

The setup files are explicitly excluded by the tracked `.gitignore` as
"Local-only delivery setup (deferred from this release)". No history for those
paths was found in available local Git refs, and the current remote main tree
does not include them. No claim is made that these utilities are recovered.
They could contain unfinished local work; absence does not establish that the
deployed application lost its implementation. Credentials must not be recreated,
guessed, copied into a report or committed.

The ignored `_reviews/` inventory included ChatGPT/Claude/Grok notes, exploratory
LPCN probes, headline notes and extracted page images. Exact originals were not
recovered in this task; the two audit documents above are independent originals
already preserved in the cloud. No production code change should be fabricated
to replace missing review scratch files.

## Preservation policy

Store important audit reports, release checklists, evidence indexes and final
decisions in tracked `docs/` with baseline SHA/date and clear historical labels.
Original reports should be imported only through supported authenticated file
transfer, preserving provenance. Do not substitute a summary under an original
filename or claim a verbatim reconstruction.

Use private evidence storage for raw provider responses/DB snapshots and large
artifacts. Include checksums and source/run identity. Keep secrets and private
delivery credentials outside Git. Generated caches, bytecode and disposable
explorations remain ignored; final decisions must graduate out of those folders.

This task found and read the original cloud reports, but supported local
materialization failed (transfer helper could not access prepare_materialize).
Local Git authentication also failed; browser access was denied. Consequently
the originals were not silently re-created as unauthenticated local copies and
no GitHub publication is claimed. Their cloud links above remain the source.
