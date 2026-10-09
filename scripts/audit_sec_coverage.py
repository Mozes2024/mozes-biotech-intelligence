"""Bounded inventory against a frozen read-only checkpoint, with durable progress."""
import argparse
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import closing
from datetime import date
from pathlib import Path

from mozes.ingest.edgar_latest import biotech_universe
from mozes.sec_coverage_audit import audit_issuer, stored_accessions, universe_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--max-issuers", type=int, default=310)
    args = parser.parse_args()
    identity = os.environ.get("SEC_USER_AGENT", "")
    if not identity.strip():
        raise RuntimeError("SEC_USER_AGENT")
    if not 1 <= args.max_issuers <= 500 or args.start > args.end:
        raise ValueError("invalid audit bounds")
    if args.output.exists() or args.output.resolve() == args.database.resolve():
        raise ValueError("audit output must be a new separate file")
    with closing(sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        universe = biotech_universe(conn)
        known = stored_accessions(conn)
    report = {"status": "PARTIAL", "read_only": True, "universe_sha256": universe_digest(universe),
              "universe": universe, "start_date": str(args.start), "end_date": str(args.end),
              "boundary": "inclusive filing dates, conservative outage-window superset",
              "issuers": [], "unresolved": [], "missing_accessions": []}
    last = 0.0

    def fetch(url):
        nonlocal last
        time.sleep(max(0, 1 - (time.monotonic() - last)))
        last = time.monotonic()
        request = urllib.request.Request(url, headers={"User-Agent": identity, "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("SEC submissions response exceeds bound")
        return json.loads(raw)

    ordered = sorted(universe, key=int)
    for offset in range(0, min(len(ordered), args.max_issuers), 3):
        # At most three issuers per pass; a denied source stops the entire inventory.
        for cik in ordered[offset:min(offset + 3, args.max_issuers)]:
            try:
                result = audit_issuer(cik, universe[cik], fetch=fetch, start=args.start,
                                      end=args.end, known=known)
                report["issuers"].append(result)
                report["missing_accessions"].extend({**f, "cik": cik, "ticker": universe[cik]["ticker"]}
                    for f in result["filings"] if not f["stored_capture"])
            except Exception as exc:
                error = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
                report["unresolved"].append({"cik": cik, "ticker": universe[cik]["ticker"], "error": error})
                if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 429):
                    report["stopped_on_access_restriction"] = True
                    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
                    print(json.dumps({"status": "PARTIAL", "checked": len(report["issuers"]), "blocker": error}))
                    return 1
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["status"] = "COMPLETE" if len(report["issuers"]) == len(universe) and not report["unresolved"] else "PARTIAL"
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "checked": len(report["issuers"]),
                      "universe": len(universe), "missing": len(report["missing_accessions"])}))
    return 0 if report["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
