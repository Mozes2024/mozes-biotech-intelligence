"""Refresh the checked-in, adjusted daily close evidence for the starter cases."""
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from mozes.ingest.prices import fetch_yahoo_chart

OUTPUT = Path(__file__).resolve().parents[1] / "mozes" / "data" / "starter_prices"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = {"provider": "Yahoo Chart keyless", "provider_url": "https://query1.finance.yahoo.com/v8/finance/chart/",
                "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "request_start": "2026-01-01", "request_end_exclusive": "2026-10-02", "files": {}}
    for ticker in ("KOD", "QTTB", "PHAR", "XBI"):
        rows = fetch_yahoo_chart(ticker, "2026-01-01", "2026-10-02")
        if len(rows) < 150:
            raise RuntimeError(f"too few historical prices for {ticker}: {len(rows)}")
        path = OUTPUT / f"{ticker}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=("date", "close", "volume"), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        manifest["files"][ticker] = {"rows": len(rows), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        print(ticker, len(rows))
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
