# v2D Validation Report

## Executed checks

- `python -m compileall -q mozes tests` — passed.
- `pytest -q` — passed: 209 tests.
- `git diff --check` — passed after the final whitespace fix.
- Legacy-reference scan — active product paths no longer import `mozes.scoring`.

## Coverage added

- Active engine imports shared score components instead of the legacy scoring module.
- Walk-forward evaluator records metrics and preserves disabled gates.
- Static-file traversal is rejected for the same resolver used by GET and HEAD.
- Oversized POST bodies are rejected before reading.
- Background refresh returns a run id and exposes no raw exception message.

## Intentional residual limits

- The evaluator is reporting-only; it cannot authorize trading gates.
- OOS metrics are limited to clean cases with existing case-attached data.
- Source-operation request metrics cover the CT.gov fetcher and SEC transport/cache;
  other existing fetchers are outside this bounded v2D change.
