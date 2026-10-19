# Worker base image source

The post-PR55 push CI at `33a737d3f7450f0e00466a9425787b77d5306de2`
(run `39217049760`, attempts 1 and 2) failed resolving the Python image from
ECR Public: HTTP 429, `Data limit exceeded`. This happened before any project
build step. Retrying a persistent registry quota failure is not a durable fix.

Use the Docker Official Image's primary `docker.io/library/python` source,
retaining the exact existing multi-platform index digest:
`sha256:e88e9763f943ec1834f992a4b51e0f24500486803e8bc534e5767af9ea65f6ce`.
On October 19, 2026, a Docker Registry v2 GET from Docker Hub returned an OCI
index whose raw response SHA256 matched that digest. The operating system,
Python 3.11.17 runtime, architecture selection and image contents are therefore
unchanged. No tag-only resolution, alternative-image fallback, new credentials,
dependency changes or security/test bypass is introduced. Registry outages or
Docker Hub rate limits still fail closed; digest pinning guarantees identity,
not registry availability. This removes the observed ECR-specific dependency.

## Validation and publication boundary

PR CI must pass both `test` and `worker-image` on the final reviewed head.
The worker job builds the actual Dockerfile and checks Python 3.11.17,
SSL/SQLite, application/data imports and inactive prospective capture.
Retain the run URL, tested SHA and resulting image ID in the PR validation
receipt. A local environment without Docker cannot substitute for this check.

After owner approval and merge, a successful **push** CI on current `main`
automatically qualifies for `pages.yml`. Its current-head guard, stored-data
restore, offline export, deployment and ledger acknowledgement remain intact.
PR CI and `workflow_dispatch` CI do not qualify for automatic Pages publication.
Do not dispatch Pages as part of validating this fix. A later manually dispatched
Pages run `39218366597` already succeeded on the PR55 merge SHA; that does not
resolve the failed push CI or certify this image-source change.

Rollback is a one-file registry-source revert; it restores the known ECR quota
exposure. No production data, monitor checkpoint or application behavior changes.
