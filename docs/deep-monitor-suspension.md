# Temporary deep-monitor suspension

Remove only the Monday/Wednesday/Friday 06:20 UTC scheduled trigger. Preserve the
manual workflow definition, job, permissions, concurrency and artifact name.
Manual deep runs are operationally prohibited until reviewed authoritative
artifact ownership is fixed; retaining workflow_dispatch does not authorize use.
The job now has an unconditional false condition, enforcing this suspension before
checkout, restore, source collection or artifact upload. Removing this condition
requires the ownership and provenance review below; a dispatch cannot bypass it.

Deep discovery and research refresh coverage is temporarily lost. Hot SEC/wire
monitoring and ordinary lightweight monitoring remain scheduled as before.
No substitute writer or renamed artifact is introduced. Resume the cron only
after ownership/serialization and checkpoint provenance are reviewed and tested.
Rollback is a separately reviewed restoration of the cron, never a manual deep
run or publication of a conflicting checkpoint.
