"""mozes — command line interface."""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import socketserver
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import db
from .alerts import build_alerts
from .analysis import run_all
from .backtest import full_report
from .config import DB_PATH
from .extract import extract_catalyst_statements
from .paper import PaperBook
from .versions import DATASET_AS_OF, RUNUP_EDGE_VALIDATED, VERSIONS

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _today(args):
    return date.fromisoformat(args.as_of) if getattr(args, "as_of", None) else date.today()


def cmd_score(args):
    r = run_all(_today(args))
    print(f"MOZES radar as of {r['today']}  (models {VERSIONS})")
    print(f"{'TICKER':<7}{'WINDOW':<25}{'PREC':<16}{'DATE%':>6}{'IMPACT':>8}{'EVID':>6}  CLASS")
    rows = sorted(r["live"], key=lambda a: (a["date"]["window"] or {}).get("start") or "9999")
    for a in rows:
        w = a["date"]["window"]
        win = f"{w['start']}..{w['end']}" if w else "-"
        print(f"{a['ticker']:<7}{win:<25}{str(a['date']['precision']):<16}{a['date']['confidence']:>6}"
              f"{a['impact']['score']:>8}{a['evidence']['score']:>6}  {a['classification']['cls']}")
    if args.store:
        conn = db.connect(DB_PATH)
        for a in r["live"]:
            db.store_score_run(conn, a)
        print(f"stored {len(r['live'])} score runs in {DB_PATH}")
    return 0


def cmd_backtest(args):
    r = run_all(_today(args))
    rep = full_report(r["historical"], r["historical_events"], r["outcomes"])
    print(json.dumps({k: rep[k] for k in ("coverage", "calibration", "class_vs_outcome", "caveats")}, indent=2))
    print("hold-through by clinical outcome:")
    for g in rep["hold_through"]["clinical_outcome"]:
        print(f"  {g['key']:<8} n={g['n']:<3} with_move={g['n_move']:<3} median={g.get('median')} mean={g.get('mean')}")
    print("run-up:", rep["run_up"]["status"])
    return 0


def cmd_alerts(args):
    r = run_all(_today(args))
    for x in build_alerts(r["live"], r["today"]):
        extra = f"{x['change']['from']} -> {x['change']['to']}" if x["kind"] == "change" else f"days_to_start={x['days_to_start']}"
        print(f"[{x['level']:<13}] {x['ticker']:<6} {x['type']:<11} impact={x['impact']:<3} {extra}")
    return 0


def build_payload(today):
    r = run_all(today)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "today": r["today"],
        "dataset_as_of": DATASET_AS_OF, "versions": VERSIONS, "runup_edge_validated": RUNUP_EDGE_VALIDATED,
        "live": r["live"], "historical": r["historical"], "outcomes": r["outcomes"], "sources": r["sources"],
        "backtest": full_report(r["historical"], r["historical_events"], r["outcomes"]),
        "alerts": build_alerts(r["live"], r["today"]),
    }


def cmd_export_ui(args):
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    out = WEB_DIR / "data.json"
    out.write_text(json.dumps(build_payload(_today(args)), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def cmd_serve(args):
    cmd_export_ui(args)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB_DIR))
    with socketserver.TCPServer(("127.0.0.1", args.port), handler) as httpd:
        print(f"MOZES UI: http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
        httpd.serve_forever()


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
    r = run_all(_today(args))
    a = next((x for x in r["live"] if x["id"] == args.id), None)
    if not a:
        print(f"unknown live catalyst id: {args.id}")
        return 1
    sid = PaperBook(db.connect(DB_PATH)).record(a, price=args.price)
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



def cmd_refresh_v2(args):
    from .radar import bootstrap_database
    from .refresh import refresh_live
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    result = refresh_live(conn, start=args.start, end=args.end, months=args.months, do_sec_map=not args.no_sec_map, do_sec_verify=not args.no_sec_verify)
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "OK" else 2



def cmd_export_v2(args):
    from .payload_v2 import build
    from .radar import bootstrap_database
    conn = db.connect(DB_PATH)
    bootstrap_database(conn)
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    out = WEB_DIR / "data_v2.json"
    out.write_text(json.dumps(build(conn, _today(args)), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def cmd_serve_v2(args):
    cmd_export_v2(args)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WEB_DIR))
    with socketserver.TCPServer(("127.0.0.1", args.port), handler) as httpd:
        print(f"MOZES v0.2 UI: http://127.0.0.1:{args.port}/index_v2.html")
        httpd.serve_forever()



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
    serve(port=args.port, host=args.host, db_path=DB_PATH)
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
    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
