"""Model versions. Bump on ANY scoring change; every stored score carries these."""
VERSIONS = {
    "catalyst_impact": "0.2.0",
    "clinical_evidence": "0.2.0",
    "market_setup": "0.2.0",
    "classifier": "0.2.0",
    "dataset": "2026-09-30-v02",
}
DATASET_AS_OF = "2026-09-30"

# The RUN-UP classification stays disabled until an out-of-sample walk-forward
# backtest on verified price data shows an edge. Do not flip this by hand.
RUNUP_EDGE_VALIDATED = False
