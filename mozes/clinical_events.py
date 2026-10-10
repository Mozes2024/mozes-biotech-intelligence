"""Auditable clinical candidates and upcoming milestones; no per-event AI calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from . import db
from .dates import normalize_date_text
from .materiality import classify_outcome

RULES = json.loads((Path(__file__).parent / "data/clinical_event_rules.json").read_text())


def classify_clinical(text, outcome=None):
    sample = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", text or ""))[:4000]
    sample = re.sub(r"\bno\s+(?:new\s+)?clinical\s+data\b", "no new information", sample, flags=re.I)
    has = lambda key, value=sample: bool(re.search(RULES[key], value, re.I))
    outcome = outcome or classify_outcome(sample)
    if outcome.get("event_family") == "corporate_action":
        return {**outcome, "relevant": True, "material": True, "actionable": False,
                "catalyst": False, "kind": "conditional_cvr", "suppression_reason": None}
    future_match = re.search(RULES['future'], sample[:700], re.I)
    confirmed = re.search(RULES['confirmed'], sample[:220], re.I)
    future = bool(future_match and (not confirmed or future_match.start() < confirmed.start()))
    relevant = has("clinical")
    routine = has("conference") and has("routine") and not has("data") and not has("regulatory")
    management = has("management", sample[:220]) and not has("data", sample[:220]) and not has("milestone", sample[:220])
    catalyst = relevant and future and (has("data") or has("regulatory") or has("milestone")) and not routine and not management
    regulatory_event = has("regulatory_event") and outcome.get("event_family") in {"fda_decision", "regulatory_milestone", "material_safety"}
    actionable = not future and not routine and not management and (bool(outcome["material"]) or regulatory_event)
    material = actionable or catalyst and (has("important") or has("regulatory")) or relevant and has('milestone') and has('important') and not routine and not management
    kind = "routine_conference" if routine else "management_update" if management else "upcoming_catalyst" if catalyst else "confirmed_development" if actionable else "clinical_update" if relevant else "outside_scope"
    return dict(outcome, relevant=relevant, material=material, actionable=actionable, catalyst=catalyst,
                polarity="unknown" if future or routine or management else outcome["polarity"], kind=kind,
                suppression_reason="routine_conference" if routine else "outside_scope" if not relevant or management else "low_materiality" if not material else None,
                method=RULES["version"])


def _id(prefix, value):
    return prefix + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def resolve_issuer(conn, headline, summary="", ticker=None):
    """Only unique SEC equity evidence; an exchange symbol is a hint, never proof."""
    from .universe import normalize_org
    text = " " + normalize_org(headline + " " + summary[:800]) + " "
    symbols = set(re.findall(r"\b(?:NASDAQ|NYSE)\s*[:：]\s*([A-Z][A-Z0-9]{0,9})\b", headline + " " + summary[:800], re.I))
    symbols = {s.upper() for s in symbols}
    if len(symbols) > 1:
        return None
    rows = [dict(r) for r in conn.execute("SELECT * FROM sponsor_ticker_map WHERE source='SEC-v2C-equity' AND confidence>=0.85 AND cik IS NOT NULL")]
    matches = {}
    for row in rows:
        name = normalize_org(row["sponsor"])
        named = len(name) >= 6 and " " + name + " " in text
        # A supplied ticker still requires company evidence for incoming wires.
        headline_name = " " + normalize_org(headline) + " "
        if named and (symbols or headline_name.startswith(" " + name + " ")) and (not symbols or row["ticker"] in symbols) and (not ticker or ticker == row["ticker"]):
            matches[(row["ticker"], str(int(row["cik"])))] = row
    if len(matches) != 1:
        return None
    issuer = next(iter(matches.values()))
    # Identifier verification is distinct from the tradability/P1 alert gate.
    lifecycle = db.security_lifecycle_row(conn, issuer["ticker"])
    if lifecycle and lifecycle["status"] in {"DELISTED", "ACQUIRED", "SUSPENDED"}:
        return None
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='v2c_security_candidates'").fetchone():
        candidates = conn.execute("SELECT eligibility FROM v2c_security_candidates WHERE ticker=?", (issuer["ticker"],)).fetchall()
        if candidates and (len(candidates) != 1 or candidates[0][0] != "eligible"):
            return None
    return issuer


def _transition(conn, event_id, state, reason=None):
    row = conn.execute("SELECT lifecycle,suppression_reason FROM clinical_events WHERE event_id=?", (event_id,)).fetchone()
    if row and row["lifecycle"] == state and row["suppression_reason"] == reason:
        return
    stamp = db.utcnow()
    conn.execute("UPDATE clinical_events SET lifecycle=?,suppression_reason=?,updated_at=? WHERE event_id=?", (state, reason, stamp, event_id))
    conn.execute("INSERT INTO clinical_event_history(event_id,lifecycle,reason,recorded_at) VALUES(?,?,?,?)", (event_id, state, reason, stamp))
    conn.commit()


def source_issuer(conn, ticker, source_type, source_url):
    """A registry-bound IR host or exact SEC CIK path can identify generic titles."""
    if not ticker or source_type not in {'sec', 'company_ir'}:
        return None
    rows = conn.execute("SELECT * FROM sponsor_ticker_map WHERE ticker=? AND source='SEC-v2C-equity' AND confidence>=0.85 AND cik IS NOT NULL", (ticker,)).fetchall()
    identities = {(r['ticker'], str(int(r['cik']))) for r in rows}
    if len(identities) != 1:
        return None
    issuer = dict(rows[0])
    parts = urlsplit(source_url or '')
    if parts.scheme != 'https' or parts.username or parts.port not in (None, 443):
        return None
    lifecycle = db.security_lifecycle_row(conn, ticker)
    if lifecycle and lifecycle['status'] in {'DELISTED', 'ACQUIRED', 'SUSPENDED'}:
        return None
    if source_type == 'sec':
        return issuer if parts.hostname == 'www.sec.gov' and re.match(r'^/Archives/edgar/data/' + str(int(issuer['cik'])) + r'/\d{18}/', parts.path) else None
    from .ir_registry import load_ir_registry
    registered = load_ir_registry().get(ticker, {})
    host = urlsplit(registered.get('site') or '').hostname
    return issuer if host and parts.hostname == host else None


def catalyst_window(text, published_at):
    """Date labels win over a conference year; quarter stays a quarter."""
    ref = date.fromisoformat(published_at[:10])
    label = re.search(r"\bDate:\s*([^\n]+)", text, re.I)
    range_match = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})\s*[-–]\s*(\d{1,2}),?\s+(20\d{2})", text, re.I)
    if label:
        chosen = label[1]
    elif range_match:
        month, start, end, year = range_match.groups()
        a = normalize_date_text(f"{month} {start}, {year}", ref)
        b = normalize_date_text(f"{month} {end}, {year}", ref)
        return dict(original=range_match[0], precision="window", start=a.start, end=b.end, confidence=80, inferred_year=False)
    else:
        # Keep the timing statement distinct from an article publication date.
        chosen = next((s for s in re.split(r"[\n.!?]", text) if re.search(RULES["future"], s, re.I) and re.search(RULES["clinical"], s, re.I)), "")
    result = asdict(normalize_date_text(chosen, ref, confirmed_by_company=True))
    return result


def publication_timestamp(value):
    if len(value)==10:return date.fromisoformat(value).isoformat()
    try:stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:stamp=parsedate_to_datetime(value)
    return stamp.astimezone(timezone.utc).isoformat() if stamp.tzinfo else stamp.date().isoformat()


def ingest_clinical(conn, *, headline, summary="", source_url, published_at, source_type,
                    ticker=None, issuer=None, edge_event_id=None, trusted_source=True, publish=True):
    """Persist before resolution. Repeated evidence may promote an earlier suppression."""
    from .primary_feeds import canonical_url
    from .live_monitor import record_change
    trusted_source = trusted_source and source_type in {'wire','company_ir','sec','fda'}
    source_url = canonical_url(source_url)
    published_at = publication_timestamp(published_at)
    event_id = _id("CLN-", [source_url, published_at])
    text = headline + "\n" + summary
    content_hash = hashlib.sha256(text.encode()).hexdigest()
    previous = conn.execute("SELECT * FROM clinical_events WHERE event_id=?", (event_id,)).fetchone()
    now = db.utcnow()
    if previous is None:
        with conn:
            conn.execute("INSERT INTO clinical_events(event_id,source_url,source_type,published_at,first_seen_at,updated_at,headline,summary,lifecycle,content_hash,edge_event_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (event_id, source_url, source_type, published_at, now, now, headline[:500], summary[:8000], "DISCOVERED", content_hash, edge_event_id))
            conn.execute("INSERT INTO clinical_event_history(event_id,lifecycle,recorded_at) VALUES(?,'DISCOVERED',?)", (event_id, now))
    scope = classify_clinical(text)
    issuer = issuer or resolve_issuer(conn, headline, summary, ticker)
    same_policy = previous and json.loads(previous['classification_json'] or '{}').get('method') == RULES['version']
    if previous and previous['catalyst_id'] and not scope['catalyst']:
        with conn:
            conn.execute('INSERT INTO clinical_catalyst_suppressions VALUES(?,?,?) ON CONFLICT(catalyst_id) DO UPDATE SET reason=excluded.reason,updated_at=excluded.updated_at',
                         (previous['catalyst_id'],'reclassified_not_upcoming',now))
    if same_policy and issuer is None and previous['content_hash']==content_hash and previous['lifecycle']=='SUPPRESSED':
        return {'event_id':event_id,'scope':scope,'change_id':None,'new_issuer':False}
    if same_policy and issuer and previous["content_hash"] == content_hash and previous["ticker"] == issuer["ticker"] and previous["suppression_reason"] != "issuer_unresolved":
        return {"event_id": event_id, "scope": scope, "change_id": previous["change_id"], "catalyst_id": previous["catalyst_id"], "new_issuer": False}
    if issuer is None:
        if not scope['relevant']:
            with conn:
                conn.execute('UPDATE clinical_events SET classification_json=? WHERE event_id=?',(json.dumps(scope),event_id))
            _transition(conn,event_id,'SUPPRESSED',scope['suppression_reason'] or 'outside_scope')
            return {'event_id':event_id,'scope':scope,'change_id':None,'new_issuer':False}
        with conn:
            conn.execute('UPDATE clinical_events SET headline=?,summary=?,content_hash=?,classification_json=? WHERE event_id=?',(headline[:500],summary[:8000],content_hash,json.dumps(scope),event_id))
        hint = re.search(r"\b(?:NASDAQ|NYSE)\s*[:：]\s*([A-Z][A-Z0-9]{0,9})\b", text[:1300], re.I)
        with conn:
            conn.execute("INSERT INTO issuer_discovery_queue(event_id,ticker_hint,status,attempts,updated_at) VALUES(?,?,'pending',1,?) ON CONFLICT(event_id) DO UPDATE SET attempts=MIN(attempts+1,20),updated_at=excluded.updated_at",
                         (event_id, hint[1].upper() if hint else None, now))
        _transition(conn, event_id, "SUPPRESSED", "issuer_unresolved")
        return {"event_id": event_id, "scope": scope, "change_id": None, "new_issuer": False}
    ticker = issuer["ticker"]
    new_issuer = not conn.execute("SELECT 1 FROM watch_universe WHERE ticker=? AND active=1", (ticker,)).fetchone()
    if scope["relevant"] and trusted_source and new_issuer:
        db.upsert_watch(conn, ticker, cik=issuer["cik"], company=issuer["sponsor"], source="clinical_announcement_discovery")
    with conn:
        conn.execute("UPDATE clinical_events SET ticker=?,cik=?,headline=?,summary=?,content_hash=?,classification_json=?,edge_event_id=COALESCE(edge_event_id,?) WHERE event_id=?",
                     (ticker, issuer["cik"], headline[:500], summary[:8000], content_hash, json.dumps(scope), edge_event_id, event_id))
        conn.execute("UPDATE issuer_discovery_queue SET status='verified',updated_at=? WHERE event_id=?", (now, event_id))
    _transition(conn, event_id, "RESOLVED")
    _transition(conn, event_id, "CLASSIFIED")
    if not trusted_source or not scope["relevant"] or not scope["material"]:
        _transition(conn, event_id, "SUPPRESSED", "classification_uncertain" if not trusted_source else scope["suppression_reason"] or "low_materiality")
        return {"event_id": event_id, "scope": scope, "change_id": None, "new_issuer": bool(new_issuer)}
    if not publish:
        _transition(conn, event_id, "ENRICHED")
        return {"event_id": event_id, "scope": scope, "change_id": previous["change_id"] if previous else None, "new_issuer": bool(new_issuer)}
    catalyst_id = None
    if scope["catalyst"]:
        window = catalyst_window(text, published_at)
        program = re.search(r"\b(?i:phase)\s*[123](?:/[23])?\s+([A-Z][A-Z0-9-]{2,})\b", text)
        program = program[1] if program else None
        if program in {'DATA','RESULTS','TOPLINE','TRIAL','STUDY'}:
            program = None
        kind = "CONFERENCE" if re.search(RULES["conference"], text, re.I) else "REGULATORY" if re.search(RULES["regulatory"], text, re.I) else 'ENROLLMENT' if re.search(r'enroll?ment',text,re.I) else 'TRIAL_COMPLETION' if re.search(r'trial\s+completion',text,re.I) else "READOUT"
        identity = [ticker, program, kind, published_at[:4]] if program else [ticker, kind, source_url]
        catalyst_id = _id("CAT-", identity)
        with conn:
            conn.execute("INSERT INTO clinical_catalysts(catalyst_id,ticker,cik,program,event_type,headline,window_json,first_seen_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(catalyst_id) DO UPDATE SET headline=excluded.headline,window_json=CASE WHEN json_extract(excluded.window_json,'$.precision')='unknown' THEN clinical_catalysts.window_json ELSE excluded.window_json END,updated_at=excluded.updated_at",
                         (catalyst_id, ticker, issuer["cik"], program, kind, headline[:500], json.dumps(window), now, now))
            conn.execute("INSERT INTO clinical_catalyst_sources VALUES(?,?,?,?,?) ON CONFLICT(catalyst_id,event_id) DO UPDATE SET source_hash=excluded.source_hash", (catalyst_id, event_id, source_url, published_at, content_hash))
            conn.execute('DELETE FROM clinical_catalyst_suppressions WHERE catalyst_id=?',(catalyst_id,))
        identity = ["clinical_catalyst", catalyst_id, window["start"], window["end"]]
    else:
        identity = ["clinical_result", ticker, content_hash]
    _transition(conn, event_id, "ENRICHED")
    change_id = record_change(conn, ticker=ticker, change_type="clinical_catalyst_signal" if catalyst_id else "wire_release_signal",
        previous_value=None, new_value={"headline": headline, "summary": summary[:8000], "published_at": published_at, "outcome": scope,
                                      "clinical_event_id": event_id, "catalyst_id": catalyst_id},
        source_url=source_url, source_type=source_type, severity="medium" if catalyst_id else "high",
        verification_state="primary_source" if source_type in {"sec", "company_ir"} else "investigation_only",
        source_hash=content_hash, identity=identity, metadata={"clinical_event_id": event_id, "source_published_at": published_at})
    with conn:
        conn.execute("UPDATE clinical_events SET change_id=?,catalyst_id=? WHERE event_id=?", (change_id, catalyst_id, event_id))
    _transition(conn, event_id, "PUBLISHED")
    return {"event_id": event_id, "scope": scope, "change_id": change_id, "catalyst_id": catalyst_id, "new_issuer": bool(new_issuer)}


def bind_change(conn, event_id, change_id):
    row=conn.execute("SELECT classification_json,lifecycle,change_id FROM clinical_events WHERE event_id=?",(event_id,)).fetchone()
    if not row or row['lifecycle']=='SUPPRESSED':
        return
    if row['lifecycle']=='PUBLISHED' and row['change_id']==change_id:return
    with conn:
        conn.execute("UPDATE clinical_events SET change_id=? WHERE event_id=?",(change_id,event_id))
    _transition(conn,event_id,'PUBLISHED')


def retry_unresolved(conn, limit=8):
    rows = conn.execute("SELECT e.* FROM clinical_events e JOIN issuer_discovery_queue q USING(event_id) WHERE q.status='pending' ORDER BY q.updated_at LIMIT ?", (limit,)).fetchall()
    results=[]
    for r in rows:
        with conn:
            conn.execute("UPDATE issuer_discovery_queue SET attempts=MIN(attempts+1,20),updated_at=? WHERE event_id=?",(db.utcnow(),r['event_id']))
        results.append(ingest_clinical(conn,headline=r['headline'],summary=r['summary'],source_url=r['source_url'],
                                      published_at=r['published_at'],source_type=r['source_type']))
    return results


def clinical_payload(conn, limit=100):
    rows = [dict(r) for r in conn.execute("SELECT c.* FROM clinical_catalysts c WHERE NOT EXISTS (SELECT 1 FROM clinical_catalyst_suppressions s WHERE s.catalyst_id=c.catalyst_id) ORDER BY updated_at DESC LIMIT ?", (limit,))]
    for row in rows:
        row["window"] = json.loads(row.pop("window_json"))
        row["sources"] = [dict(r) for r in conn.execute("SELECT source_url,published_at,source_hash FROM clinical_catalyst_sources WHERE catalyst_id=?", (row["catalyst_id"],))]
    counts = dict(conn.execute("SELECT lifecycle,COUNT(*) FROM clinical_events GROUP BY lifecycle").fetchall())
    suppressed = dict(conn.execute("SELECT suppression_reason,COUNT(*) FROM clinical_events WHERE lifecycle='SUPPRESSED' GROUP BY suppression_reason").fetchall())
    observations=[json.loads(r[0]) for r in conn.execute("SELECT value_json FROM monitor_observations WHERE observation_key LIKE 'wire_feed:%' OR observation_key LIKE 'official_feed:%' OR observation_key LIKE 'sec_latest:%' OR observation_key LIKE 'fda_feed:%'")]
    def stale(value):
        stamp=value.get('last_checked_at') or value.get('checked_at')
        try:return datetime.now(timezone.utc)-datetime.fromisoformat(stamp.replace('Z','+00:00'))>timedelta(minutes=30)
        except (ValueError,TypeError,AttributeError):return True
    return {"catalysts": rows, "operations": {"events_discovered": conn.execute("SELECT COUNT(*) FROM clinical_events").fetchone()[0],
        "by_lifecycle": counts, "suppressed_by_reason": suppressed, "issuers_resolved": conn.execute("SELECT COUNT(DISTINCT ticker) FROM clinical_events WHERE ticker IS NOT NULL").fetchone()[0],
        "events_classified":conn.execute('SELECT COUNT(*) FROM clinical_events WHERE classification_json IS NOT NULL').fetchone()[0],
        "events_published":counts.get('PUBLISHED',0),'source_failures':sum(v.get('status') in {'fetch_error','FAILED'} for v in observations),'stale_feeds':sum(stale(v) for v in observations),
        "new_issuers_discovered": conn.execute("SELECT COUNT(*) FROM watch_universe WHERE source='clinical_announcement_discovery'").fetchone()[0],
        "clinical_catalysts": conn.execute("SELECT COUNT(*) FROM clinical_catalysts").fetchone()[0],
        'catalysts_created_or_updated':conn.execute('SELECT COUNT(*) FROM clinical_catalyst_sources').fetchone()[0],
        'rejected_before_issuer_resolution':conn.execute("SELECT COUNT(*) FROM clinical_events WHERE ticker IS NULL").fetchone()[0],
        "notification_failures": conn.execute("SELECT COUNT(*) FROM alert_outbox WHERE status IN ('failed','dead')").fetchone()[0]}}


def investigate(conn, query):
    pattern = '%' + query[:200].replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
    return [dict(r) for r in conn.execute("SELECT * FROM clinical_events WHERE ticker=? OR headline LIKE ? ESCAPE '\\' OR source_url LIKE ? ESCAPE '\\' ORDER BY first_seen_at DESC LIMIT 30", (query.upper(), pattern, pattern))]


def retain_candidates(conn, now=None, max_rows=3000, days=14):
    cutoff = ((now or datetime.now(timezone.utc))-timedelta(days=days)).isoformat()
    with conn:
        conn.execute("DELETE FROM clinical_events WHERE lifecycle IN ('SUPPRESSED','PUBLISHED') AND (first_seen_at<? OR event_id IN (SELECT event_id FROM clinical_events WHERE lifecycle IN ('SUPPRESSED','PUBLISHED') ORDER BY first_seen_at DESC LIMIT -1 OFFSET ?))", (cutoff,max_rows))
        conn.execute('DELETE FROM edge_candidate_receipts WHERE processed_at<?', (cutoff,))


def reclassify_policy(conn, limit=20):
    rows=conn.execute("SELECT * FROM clinical_events WHERE json_extract(classification_json,'$.method')<>? ORDER BY first_seen_at LIMIT ?",(RULES['version'],limit)).fetchall()
    for row in rows:
        scope=classify_clinical(row['headline']+'\n'+row['summary'])
        result=ingest_clinical(conn,headline=row['headline'],summary=row['summary'],source_url=row['source_url'],
            published_at=row['published_at'],source_type=row['source_type'],ticker=row['ticker'],publish=scope['catalyst'])
        if row['change_id'] and not row['catalyst_id']:
            bind_change(conn,result['event_id'],row['change_id'])
    return {'reclassified':len(rows)}


if __name__ == "__main__":
    from .config import DB_PATH
    import os
    parser=argparse.ArgumentParser()
    parser.add_argument("query")
    args=parser.parse_args()
    conn=db.connect(os.environ.get("MOZES_DB_PATH",DB_PATH))
    try: print(json.dumps(investigate(conn,args.query),ensure_ascii=False))
    finally: conn.close()
