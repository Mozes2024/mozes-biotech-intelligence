# MOZES Live Intelligence v2A — upload bundle

Base reviewed: `main` at `577b11c0c0ff20c98776960313da7c75b2dc41a2`.

Upload these files to the exact same paths in GitHub:

- `mozes/live_intelligence.py` — new
- `mozes/payload_v3.py` — replace existing
- `web/live_intelligence_ui.js` — new
- `web/index.html` — replace existing
- `tests/test_live_intelligence.py` — new

What this adds:

1. **Market Attention context** using existing daily price/XBI data: latest-session move, 20-day relative volume, 20-day breakout, relative performance vs XBI, and a transparent descriptive attention score.
2. **Post-event chase warning** when the stock has already made a very large recent move. This is context only and does not change evidence scores, recommendations, or validation gates.
3. **Catalyst chain per ticker**, so multiple future events are shown as one timeline rather than looking like unrelated duplicate ideas.
4. **Recent completed catalyst context** from clean historical cases, including the case-bound stock move when available.
5. **Dashboard de-duplicates by ticker**. KOD therefore appears once, with an explicit message that DAYBREAK already happened and that the remaining reason for monitoring is the future BLA/PEAK catalyst chain.

After upload, GitHub should trigger CI + Pages automatically. Expected safety invariant: no change to RUN-UP/HOLD gate logic and no change to recommendation thresholds.

Recommended checks after upload:

```bash
pytest -q
python -m py_compile mozes/live_intelligence.py mozes/payload_v3.py
node --check web/live_intelligence_ui.js
```

Operational note: SEC filing monitoring still needs the repository Actions variable `SEC_USER_AGENT` to be configured; this bundle does not work around SEC fair-access requirements.
