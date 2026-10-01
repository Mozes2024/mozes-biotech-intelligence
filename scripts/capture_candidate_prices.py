"""Capture a frozen, keyless price inventory for every ticker in the precommitted frame.

Unavailable histories stay unavailable; this script never substitutes a successor ticker.
"""
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from mozes.ingest.prices import fetch_yahoo_chart

ROOT = Path(__file__).resolve().parents[1]
FRAMES = (ROOT / "mozes/data/historical_candidate_frame_2024_2026.json",
          ROOT / "mozes/data/historical_candidate_frame_2024_2026_b.json")
OUTPUT = ROOT / "mozes/data/candidate_prices_2024_2026"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    frames = [json.loads(path.read_text(encoding="utf-8")) for path in FRAMES]
    tickers = sorted({c["ticker"] for frame in frames for c in frame["candidates"]} | {"XBI"})
    manifest = {
        "frame_ids": [frame["frame_id"] for frame in frames], "provider": "Yahoo Chart keyless",
        "provider_url": "https://query1.finance.yahoo.com/v8/finance/chart/",
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "request_start": "2024-01-01", "request_end_exclusive": "2026-10-02",
        "files": {}, "unavailable": {},
    }
    for ticker in tickers:
        try:
            rows = fetch_yahoo_chart(ticker, "2024-01-01", "2026-10-02")
            if not rows:
                raise ValueError("empty history")
        except Exception as exc:
            manifest["unavailable"][ticker] = f"{type(exc).__name__}: {exc}"
            print(ticker, "UNAVAILABLE", exc)
            continue
        path = OUTPUT / f"{ticker}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=("date", "close", "volume"), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        manifest["files"][ticker] = {"rows": len(rows), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        print(ticker, len(rows))
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
