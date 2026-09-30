# Research Roadmap

## Stage 1 - Clean historical catalog

Target 200-500 resolved events across multiple years and therapeutic areas.

For each event freeze:

- primary-source event guidance chronology;
- exact publication timestamp/session when available;
- trial design and prior evidence known at the time;
- balance-sheet and dilution context known at the time;
- verified adjusted closes and benchmark prices;
- outcome label stored separately.

Annotations should be blinded to the outcome.

## Stage 2 - Descriptive research

Measure separately:

- pre-catalyst drift;
- event gap and close-to-close move;
- post-event drift;
- readout vs PDUFA vs AdCom behavior;
- market-cap and dependency effects;
- therapeutic-area differences;
- design-drift penalties;
- date-precision tightening as a feature.

## Stage 3 - Calibration

Evaluate evidence buckets with walk-forward splits. Use Brier score/log loss only after a proper probability model exists.

## Stage 4 - Paper tape

Freeze live snapshots at T-60/T-30/T-14/T-7/T-3/T-1. Never edit old predictions after outcomes are known.

## Stage 5 - Capital gate

Only after stable out-of-sample evidence should any automated trade-oriented classification be enabled. Position sizing and portfolio-level tail risk need a separate validation layer.
