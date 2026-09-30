# Methodology (v0.1)

All scores are **heuristic, documented, not fitted and not calibrated**. Each score returns its
contribution list so any number can be audited in the UI.

## Catalyst Impact (0–100)
Additive, capped at 100:

| Factor | Points |
|---|---|
| Type | P3 topline 30 · PDUFA new product 26 · AdCom 24 · P2 topline 22 · P1/2 20 · Interim 20 · Supplemental PDUFA 12 · Conference 10 · Filing 8 |
| Pivotal Phase 2 or 1/2 | +4 |
| Program dependency | single 25 · lead 18 · one of few 12 · one of many 5 |
| Market cap (pre-event) | micro/small 20 · mid 16 · large 10 · mega 3 · unknown 8 |
| Stage | pre-commercial 10 · commercial 3 |
| Success directly enables filing/approval | +10 |
| Cash runway < 12 months | +5 |

## Clinical Evidence (5–95)
Start: base rate × therapeutic-area multiplier.
- Phase II→III 30.7%, Phase III→NDA/BLA 58.1%, NDA/BLA→approval 85.3% (BIO/Biomedtracker/Amplion, 2006–2015).
- These are **phase-transition** rates, not "positive topline" rates. Supplemental PDUFA, AdCom, Interim and Conference bases are unsourced placeholders.
- Therapeutic-area multipliers: oncology 0.8, hematology 1.15, neurology/psychiatry 0.9, neuromuscular 1.05. The direction follows the BIO reports; the magnitudes are assumptions.

Adjustments: prior efficacy (strong +12 / moderate +5 / weak −8 / none −12); endpoint same +6, partial +2, changed −8;
population same +4, changed −8; regimen changed −4; design (placebo/sham RCT +4, active NI +2, single-arm or external −6);
objective endpoint ±3; mechanism validated +6, partial +2, novel −3; class failures −4; safety signal −6; n<100 −4;
SPA +4, BTD +3, RMAT +2; prior CRL −6; negative AdCom −30; FDA-stated requirement at risk −25; data-integrity concern −15.

Categories: Weak <40, Moderate 40–59, Strong 60–79, Very Strong ≥80.
The "model estimate" shrinks the score toward the base rate. Its band widens as feature completeness drops.

## Classification (skeptical)
- **AVOID** — any critical flag; evidence < 45; or the reference-class bear midpoint < −60% with evidence < 70.
- **HOLD** — only if evidence ≥ 75, asymmetry ≥ 2, date confidence ≥ 80, market data available, and no high flags.
- **RUN-UP** — blocked while `RUNUP_EDGE_VALIDATED = False`.
- **WATCH** — otherwise, with explicit reasons.

## Scenarios
The reference class uses only events resolved **before** the as-of date. It is scaled by an exposure factor (program dependency × market-cap damping).
The seed data is biased toward famous large moves, and it ignores market-implied expectations. Treat it as context only.

## Point-in-time rules
See `mozes/pit.py` and `tests/test_leakage.py` (release-blocking).
