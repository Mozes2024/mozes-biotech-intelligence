"""mozes — command line interface."""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import socketserver
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import db
from .alerts import build_alerts
from .config import DB_PATH
from .extract import extract_catalyst_statements
from .paper import PaperBook
from .versions import DATASET_AS_OF, VERSIONS

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _today(args):
    return date.fromisoformat(args.as_of) if getattr(args, "as_of", None) else date.today()


def cmd_score(args):
    """Compatibility alias; scoring is now exclusively database-backed v2."""
    return cmd_radar_v2(args)


def cmd_backtest(args):
    from .backtest_v2 import report
    from .radar import bootstrap_database
    conn = db.connect(DB_PATH); bootstrap_database(conn)
    print(json.dumps(report(conn), indent=2))
    return 0


def cmd_alerts(args):
    from .engine_v2 import score_event
    from .radar import bootstrap_database, live_event_records
    conn = db.connect(DB_PATH); bootstrap_database(conn)
    for row in build_alerts([score_event(conn, event, _today(args).isoformat()) for event in live_event_records(conn)], _today(args).isoformat()):
        print(json.dumps(row, ensure_ascii=False))
    return 0


def build_payload(today):
    from .payload_v3 import build
    from .radar import bootstrap_database
    conn = db.connect(DB_PATH); bootstrap_database(conn)
    return build(conn, today)


def cmd_export_ui(args):
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    out = WEB_DIR / "data.json"
    out.write_text(json.dumps(build_payload(_today(args)), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def cmd_serve(args):
    return cmd_app_v3(args)


def cmd_extract(args):
    text = Path(args.file).read_text(encoding="utf-8")
    out = extract_catalyst_statements(text, date.fromisoformat(args.ref), args.source, args.reliability)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_seed(args):
    from .data_loader import load_historical, load_live, load_outcomes, load_sources
    conn = db.connect(DB_PATH)
    db.seed(conn, load_sources(), load_live(), load_historical(), load_outcomes())
    print(f"seeded {DB_PATH}")
    return 0


def cmd_paper_record(args):
    from .engine_v2 import score_event
    from .radar import bootstrap_database, live_event_records
    conn = db.connect(DB_PATH); bootstrap_database(conn)
    event = next((row for row in live_event_records(conn, include_quarantined=True) if row["id"] == args.id), None)
    if not event:
        print(f"unknown live catalyst id: {args.id}")
        return 1
    sid = PaperBook(conn).record(score_event(conn, event, _today(args).isoformat()), price=args.price)
    print(f"recorded immutable paper signal {sid}")
    return 0


def cmd_paper_list(args):
    for row in PaperBook(db.connect(DB_PATH)).list():
        print(row)
    return 0


def cmd_paper_outcome(args):
    PaperBook(db.connect(DB_PATH)).attach_outcome(args.signal, args.clinical, args.move, _today(args).isoformat(), args.note)
    print("outcome attached (write-once)")
    return 0


def cmd_ingest_edgar(args):
    from .ingest.edgar import extract_from_filing, recent_filings
    conn = db.connect(DB_PATH)
    for f in recent_filings(args.cik, limit=args.limit):
        stmts = extract_from_filing(f)
        db.store_statements(conn, stmts, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        for s in stmts:
            w = s["window"]
            print(f"{f['filed']} {f['form']:<5} {s['catalyst_type']:<11} {w['precision']:<16} {w['start']}..{w['end']}  {s['statement'][:110]}")
    return 0


def cmd_ingest_ctgov(args):
    from .ingest.ctgov import fetch_study, store_version
    print(json.dumps(store_version(db.connect(DB_PATH), fetch_study(args.nct)), indent=2))
    return 0



def cmd_bootstrap_v2(args):
    from .radar import bootstrap_database, validation_status
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    print(json.dumps({"db": str(DB_PATH), "events": db.count_events(conn), "validation": validation_status(conn)}, indent=2))
    return 0


def cmd_radar_v2(args):
    from .engine_v2 import score_event
    from .radar import bootstrap_database, live_event_records, current_resolved_records, validation_status
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    today = _today(args).isoformat()
    rows = [score_event(conn, e, today) for e in live_event_records(conn, include_quarantined=True)]
    rows.sort(key=lambda x: ((x.get("date") or {}).get("window") or {}).get("start") or "9999")
    print(f"MOZES v0.2 radar as of {today}")
    print(f"{'TICKER':<7}{'TYPE':<13}{'STATE':<16}{'VERIFY':<13}{'IMPACT':>7}{'EVID':>7}  CLASS")
    for a in rows:
        ev = a['evidence'].get('score')
        evs = '-' if ev is None else str(ev)
        print(f"{str(a['ticker'] or '-'):<7}{a['type']:<13}{a['state'].get('status','-'):<16}{a['state'].get('verification_state','-'):<13}{a['impact']['score']:>7}{evs:>7}  {a['classification']['class']}")
    print("validation gates:", json.dumps(validation_status(conn)))
    resolved = current_resolved_records(conn)
    if resolved:
        print("resolved live-seed events excluded:", ", ".join(x['id'] for x in resolved))
    return 0


def cmd_discover_v2(args):
    from .discovery import discover, store_candidates
    from .radar import bootstrap_database
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    maps = [dict(r) for r in conn.execute("SELECT * FROM sponsor_ticker_map").fetchall()]
    rows = discover(start=args.start, end=args.end, months=args.months, sponsor_map=maps)
    store_candidates(conn, rows)
    mapped = sum(1 for x in rows if x.get('ticker'))
    print(json.dumps({"discovered": len(rows), "mapped": mapped, "unmapped": len(rows)-mapped}, indent=2))
    return 0


def cmd_validation_v2(args):
    from .radar import bootstrap_database, validation_status
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    print(json.dumps(validation_status(conn), indent=2))
    return 0


def cmd_validation_evaluate(args):
    from .radar import bootstrap_database
    from .validation_evaluator import evaluate_walk_forward
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    print(json.dumps(evaluate_walk_forward(conn, folds=args.folds), indent=2))
    return 0



def cmd_refresh_v2(args):
    from .radar import bootstrap_database
    from .refresh import refresh_live
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    result = refresh_live(conn, start=args.start, end=args.end, months=args.months, do_sec_map=not args.no_sec_map, do_sec_verify=not args.no_sec_verify)
    print(json.dumps(result, indent=2))
    result_path = os.environ.get('MOZES_RESULT_PATH')
    if result_path:
        from pathlib import Path
        tmp = Path(result_path + '.tmp')
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({'protocol': 'mozes-v2e2-result', 'run_token': os.environ.get('MOZES_RUN_TOKEN'),
                                   'status': result.get('status'), 'details': result}, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, result_path)
    return 0 if result.get("status") in {"OK", "INCOMPLETE", "BOUNDED"} else 2



def cmd_export_v2(args):
    print("export-v2 is deprecated; writing the v3 static payload instead.")
    return cmd_export_v3(args)


def cmd_serve_v2(args):
    print("serve-v2 is deprecated; starting the v3 application instead.")
    return cmd_app_v3(args)



def cmd_export_v3(args):
    from .payload_v3 import build
    from .radar import bootstrap_database
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    out = WEB_DIR / "data.json"
    out.write_text(json.dumps(build(conn, _today(args)), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def cmd_app_v3(args):
    from .app_server import serve
    serve(port=args.port, host=getattr(args, "host", "127.0.0.1"), db_path=DB_PATH)
    return 0


def cmd_prices_v2(args):
    from .ingest.prices import fetch_tiingo, fetch_yahoo_chart, load_csv
    conn = db.connect(DB_PATH)
    if args.provider == "csv":
        if not args.file:
            raise SystemExit("--file is required for provider=csv")
        rows = load_csv(args.file)
        source = args.source or "csv"
    elif args.provider == "tiingo":
        if not args.start or not args.end:
            raise SystemExit("--start and --end are required for provider=tiingo")
        rows = fetch_tiingo(args.ticker, args.start, args.end)
        source = "tiingo"
    else:
        if not args.start or not args.end:
            raise SystemExit("--start and --end are required for provider=yahoo")
        rows = fetch_yahoo_chart(args.ticker, args.start, args.end)
        source = "yahoo-chart-keyless"
    db.store_prices(conn, args.ticker.upper(), rows, source)
    print(json.dumps({"ticker": args.ticker.upper(), "rows": len(rows), "source": source}, indent=2))
    return 0


def cmd_historical_import(args):
    from .historical import import_bundle
    from .radar import bootstrap_database
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    print(json.dumps(import_bundle(conn, args.file, fetch_sources=args.fetch_sources), indent=2))
    return 0


def cmd_historical_readiness(args):
    from .historical import readiness_summary
    from .radar import bootstrap_database
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    print(json.dumps(readiness_summary(conn), indent=2))
    return 0


def cmd_historical_export(args):
    from .historical import readiness_summary
    from .radar import bootstrap_database
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    payload = readiness_summary(conn)
    if args.file:
        Path(args.file).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    else:
        print(json.dumps(payload, indent=2))
    return 0


def cmd_historical_prices(args):
    from .historical import backfill_prices
    from .radar import bootstrap_database
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    print(json.dumps(backfill_prices(conn, args.case, args.provider, stock_file=args.stock_file, benchmark_file=args.benchmark_file,
                                     source_url=args.source_url), indent=2))
    return 0


def cmd_historical_batch_import(args):
    from .historical_batch import import_checked_batch
    from .radar import bootstrap_database
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    result = import_checked_batch(conn)
    print(json.dumps({"imported_cases": result["cases"], "archived_sources": result["sources"],
                      "readiness": {key: result["readiness"][key] for key in
                                    ("cases", "research_ready", "runup_ready", "hold_ready", "legacy_quarantined")}}, indent=2))
    return 0


def cmd_historical_batch_status(args):
    from .historical_batch import batch_status
    from .radar import bootstrap_database
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    print(json.dumps(batch_status(conn), indent=2))
    return 0


def cmd_audit_securities(args):
    from .radar import bootstrap_database
    from .security import audit_current_universe
    conn = db.connect(DB_PATH); bootstrap_database(conn)
    print(json.dumps(audit_current_universe(conn, provider=args.provider), indent=2))
    return 0


def cmd_monitor_live(args):
    from .radar import bootstrap_database
    from .live_monitor import run_monitor
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    result = run_monitor(conn, audit=not args.no_audit, ctgov_diff=not args.no_ctgov,
                         sec=not args.no_sec, filings_per_company=args.filings)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "OK" else 2


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(prog="mozes", description="MOZES Biotech Catalyst Intelligence")
    sub = p.add_subparsers(dest="cmd", required=True)

    def sp(name, fn, help_):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--as-of", help="YYYY-MM-DD (default: today)")
        s.set_defaults(func=fn)
        return s

    s = sp("score", cmd_score, "score live catalysts")
    s.add_argument("--store", action="store_true", help="append score runs to the DB")
    sp("backtest", cmd_backtest, "historical backtest report")
    sp("alerts", cmd_alerts, "countdown and change alerts")
    sp("export-ui", cmd_export_ui, "write web/data.json for the Hebrew UI")
    s = sp("serve", cmd_serve, "export and serve the UI locally")
    s.add_argument("--port", type=int, default=8000)
    s = sp("extract", cmd_extract, "extract catalyst statements from a text file")
    s.add_argument("--file", required=True)
    s.add_argument("--ref", required=True, help="publication date of the text, YYYY-MM-DD")
    s.add_argument("--source", default=None)
    s.add_argument("--reliability", default="primary", choices=["primary", "secondary"])
    sp("seed", cmd_seed, "load the curated dataset into SQLite")
    s = sp("paper-record", cmd_paper_record, "record an immutable paper signal")
    s.add_argument("--id", required=True)
    s.add_argument("--price", type=float, default=None)
    sp("paper-list", cmd_paper_list, "list paper signals")
    s = sp("paper-outcome", cmd_paper_outcome, "attach a write-once outcome")
    s.add_argument("--signal", required=True)
    s.add_argument("--clinical", required=True, choices=["success", "fail", "mixed"])
    s.add_argument("--move", type=float, default=None)
    s.add_argument("--note", default="")
    s = sp("ingest-edgar", cmd_ingest_edgar, "extract catalyst statements from recent SEC filings")
    s.add_argument("--cik", required=True)
    s.add_argument("--limit", type=int, default=10)
    s = sp("ingest-ctgov", cmd_ingest_ctgov, "store a ClinicalTrials.gov record version")
    s.add_argument("--nct", required=True)
    sp("bootstrap-v2", cmd_bootstrap_v2, "initialize v0.2 database state and validation gates")
    sp("radar-v2", cmd_radar_v2, "database-driven verified catalyst radar")
    sp("validation-v2", cmd_validation_v2, "show empirical release gates")
    s = sp("validation-evaluate", cmd_validation_evaluate, "record walk-forward OOS metrics without opening gates")
    s.add_argument("--folds", type=int, default=5)
    s = sp("discover-v2", cmd_discover_v2, "discover ClinicalTrials.gov candidates (not verified catalysts)")
    s.add_argument("--start", default=None, help="YYYY-MM-DD")
    s.add_argument("--end", default=None, help="YYYY-MM-DD")
    s.add_argument("--months", type=int, default=6)
    s = sp("refresh-v2", cmd_refresh_v2, "live refresh: SEC map -> CT.gov discovery -> SEC verification")
    s.add_argument("--start", default=None, help="YYYY-MM-DD")
    s.add_argument("--end", default=None, help="YYYY-MM-DD")
    s.add_argument("--months", type=int, default=6)
    s.add_argument("--no-sec-map", action="store_true")
    s.add_argument("--no-sec-verify", action="store_true")
    sp("export-v2", cmd_export_v2, "export database-driven v0.2 UI payload")
    s = sp("serve-v2", cmd_serve_v2, "serve the Hebrew v0.2 UI")
    s.add_argument("--port", type=int, default=8000)
    sp("export-v3", cmd_export_v3, "export the v0.3 product payload for static hosting")
    s = sp("app", cmd_app_v3, "run the v0.3 Hebrew product UI + JSON API")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--host", default="127.0.0.1")
    s = sp("prices-v2", cmd_prices_v2, "ingest verified daily prices into the v0.2 database")
    s.add_argument("--ticker", required=True)
    s.add_argument("--provider", choices=["csv", "tiingo", "yahoo"], default="csv")
    s.add_argument("--file", default=None)
    s.add_argument("--start", default=None)
    s.add_argument("--end", default=None)
    s.add_argument("--source", default=None)
    s = sp("historical-import", cmd_historical_import, "import a source-archived point-in-time historical bundle")
    s.add_argument("--file", required=True)
    s.add_argument("--fetch-sources", action="store_true", help="archive source URLs when bundle content is absent")
    sp("historical-batch-import", cmd_historical_batch_import, "import the checked 2024-2026 case and price batch")
    sp("historical-batch-status", cmd_historical_batch_status, "report frozen-frame inclusion, exclusions and strata")
    sp("historical-readiness", cmd_historical_readiness, "inspect historical research/run-up/hold readiness")
    s = sp("historical-export", cmd_historical_export, "export historical dataset status JSON")
    s.add_argument("--file", default=None)
    s = sp("historical-prices", cmd_historical_prices, "backfill ticker and XBI prices for one historical case")
    s.add_argument("--case", required=True)
    s.add_argument("--provider", choices=["csv", "yahoo"], default="csv")
    s.add_argument("--stock-file", default=None)
    s.add_argument("--benchmark-file", default=None)
    s.add_argument("--source-url", default=None, help="original provider URL for CSV capture provenance")
    s = sp("audit-securities", cmd_audit_securities, "audit the live watch universe against official listing directories")
    s.add_argument("--provider", choices=["auto", "nasdaq", "sec"], default="auto")
    s = sp("monitor-live", cmd_monitor_live, "lightweight listing, registry and SEC change monitor")
    s.add_argument("--no-audit", action="store_true")
    s.add_argument("--no-ctgov", action="store_true")
    s.add_argument("--no-sec", action="store_true")
    s.add_argument("--filings", type=int, default=12)
    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
