# MOZES Biotech Catalyst Intelligence — Product Layer v0.3

v0.3 turns the research engine into a daily-use product without weakening the point-in-time rules.

## Product surfaces

- Hebrew RTL dashboard with current counts and release gates.
- Filterable Catalyst Radar.
- Event detail drawer with timeline, source provenance, evidence reasons, risk flags and market setup.
- CT.gov discovery queue clearly separated from verified catalysts.
- Resolved-event archive.
- Immutable Paper Signals visible in the UI.
- Validation/audit page.
- System/watch-universe page and local refresh control.

## Local API

`mozes app` serves the UI and JSON API on `127.0.0.1` by default.

- `GET /api/health`
- `GET /api/radar?as_of=YYYY-MM-DD`
- `GET /api/events/<event_id>`
- `GET /api/paper`
- `POST /api/paper`
- `GET /api/status`
- `POST /api/refresh`

Mutation endpoints are intended for the local product server. Public static hosting uses exported snapshots and has no mutation surface.

## Static/cloud mode

`mozes export-v3` writes `web/data.json`. The SPA automatically falls back to this file when no API server is available.

The GitHub Pages workflow builds a fresh snapshot on push and on weekday schedules. If repository variable `SEC_USER_AGENT` is configured it attempts the full SEC/CT.gov refresh; otherwise it performs CT.gov discovery and deploys the verified seed state.

## Safety/product rule

The UI never converts Impact or Evidence into a buy recommendation. RUN-UP and HOLD labels remain controlled by the empirical validation gates in the research engine.
