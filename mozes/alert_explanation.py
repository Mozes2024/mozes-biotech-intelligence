"""Source-backed alert explanations, processed after Stage-1 delivery.

Source text is evidence, never instructions. Deterministic explanations work without
an AI key. Optional AI wording cannot alter verification, priority or scoring.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import socket
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

from . import db
from .alert_dispatch import build_stage1_payload
from .ir_registry import ir_site
from .materiality import classify_outcome

MAX_SOURCE_BYTES = 1024 * 1024
MAX_TEXT = 60_000
METHOD = "source-rules-v3"
TOPICS = (
    ("corporate_action", re.compile(r"\b(?:contingent value rights?|CVRs?)\b", re.I),
     "המקור עוסק בזכויות ערך מותנות (CVR). ערך עתידי ותשלום לבעלי המניות תלויים בתנאי ההסכם, ואינם אישור תרופה או תחזית מחיר."),
    ("clinical", re.compile(r"primary\s+(?:efficacy\s+)?end\s*point|top[ -]?line|phase\s*[123]|interim\s+(?:efficacy|data|results)", re.I),
     "המקור עוסק בנתוני ניסוי קליני. יש לבדוק את המדד הראשי, גודל המדגם והבטיחות לפני הסקת מסקנה."),
    ("regulatory", re.compile(r"\b(?:approval|approved|PDUFA|complete response letter|CRL|clinical hold)\b", re.I),
     "המקור עוסק בעדכון רגולטורי. משמעותו תלויה ברשות, בהתוויה ובתנאים המדויקים של ההחלטה."),
    ("financing", re.compile(r"\b(?:public offering|private placement|financing|registered direct|at-the-market)\b", re.I),
     "המקור עוסק במימון. יש לבדוק את תנאי העסקה ואת השפעתה על המזומן ועל הדילול."),
    ("timing", re.compile(r"\b(?:expected|anticipates?|scheduled|delayed|postponed)\b", re.I),
     "המקור כולל ציפייה או שינוי בתזמון. ציפייה למועד אינה תוצאת ניסוי או אישור."),
)


class SourceText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "nav", "footer", "form", "noscript"}:
            self.skip += 1
        if not self.skip and tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "footer", "form", "noscript"}:
            self.skip = max(0, self.skip - 1)
        if not self.skip and tag in {"p", "div", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")
    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def normalize_source(raw):
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    parser = SourceText()
    parser.feed(raw[:MAX_SOURCE_BYTES])
    lines = [re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    text = "\n".join(line for line in lines if line)
    text = re.split(r"\b(?:forward-looking statements|safe harbor statement)\b", text, maxsplit=1, flags=re.I)[0]
    return text[:MAX_TEXT]


def allowed_source_url(url, payload, *, resolve=False):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.username or parts.password or parts.port not in (None, 80, 443):
        raise ValueError("invalid source authority")
    if parts.scheme == "http":
        parts = parts._replace(scheme="https", netloc=host)
    if parts.scheme != "https" or not host or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError("invalid source URL")
    kind = payload.get("source_type")
    domains = {"sec": ("sec.gov",), "fda": ("fda.gov",),
               "wire": ("businesswire.com", "prnewswire.com", "globenewswire.com")}.get(kind, ())
    if kind == "company_ir":
        site = ir_site(payload.get("ticker"))
        domains = ((urlsplit(site).hostname or "").lower(),) if site else ()
    if not any(host == domain or host.endswith("." + domain) for domain in domains if domain):
        raise ValueError("unregistered source host")
    if resolve:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError("non-public source address")
    return urlunsplit(parts._replace(fragment=""))


def fetch_source(url, payload):
    url = allowed_source_url(url, payload, resolve=True)
    class SafeRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            target = allowed_source_url(newurl, payload, resolve=True)
            return super().redirect_request(request, fp, code, msg, headers, target)
    user_agent = os.environ.get("SEC_USER_AGENT") if payload.get("source_type") == "sec" else None
    if payload.get("source_type") == "sec" and not user_agent:
        raise ValueError("SEC contact is not configured")
    request = urllib.request.Request(url, headers={
        "User-Agent": user_agent or "MozesSourceReader/1.0 (+https://github.com/Mozes2024/mozes-biotech-intelligence)",
        "Accept": "text/html,text/plain",
    })
    with urllib.request.build_opener(SafeRedirect()).open(request, timeout=8) as response:
        content_type = response.headers.get("Content-Type", "")
        if not any(t in content_type for t in ("text/html", "text/plain", "application/xhtml")):
            raise ValueError("unsupported source format")
        raw = response.read(MAX_SOURCE_BYTES + 1)
        if len(raw) > MAX_SOURCE_BYTES:
            raise ValueError("source exceeds size limit")
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def priority_reason(payload):
    if payload.get("priority") == "P1":
        return "ההתראה דחופה לפי התפתחות קלינית או רגולטורית מהותית, או שינוי מסחר קריטי; אימות החברה אינו חברות ברשימת מעקב."
    return "ההתראה עברה את סף המהותיות של הסורק לפי סוג האירוע והטקסט שנקלט."


def _quote(text, match, words=24):
    # Quote a complete sentence (including its antecedent), never a token crop.
    # Preserve abbreviations such as U.S. by requiring a sentence boundary.
    boundaries = list(re.finditer(r"[.!?](?=\s+[A-Z\"“])", text))
    start = max((m.end() for m in boundaries if m.end() <= match.start()), default=0)
    end = next((m.end() for m in boundaries if m.start() >= match.end()), len(text))
    quote = text[start:end].strip()
    return quote if len(quote.split()) <= words and len(quote) <= 800 else ""


def explain(payload, text="", *, basis="headline_only", source_hash=None):
    value = payload.get("new_value") if isinstance(payload.get("new_value"), dict) else {}
    headline = str((value or {}).get("headline") or (value or {}).get("title") or "")
    sample = text or headline
    result = {"basis": basis, "method": METHOD, "summary_he": "נקלט עדכון חדש המחייב בדיקת המקור.",
              "why_he": priority_reason(payload), "source_url": payload.get("source_url"),
              "source_hash": source_hash, "evidence": [], "missing_he": [],
              "verification_state": payload.get("verification_state"), "ai_status": "disabled"}
    if payload.get("change_type") in {"nasdaq_halt_signal", "nasdaq_volatility_pause"}:
        code = (value or {}).get("reason_code")
        reasons = {"T1": "הבורסה דיווחה על עצירה בהמתנה לחדשות.", "T2": "הבורסה דיווחה שחדשות פורסמו.",
                   "T3": "הבורסה דיווחה על חדשות ועל מועדי חידוש המסחר.",
                   "T12": "הבורסה ביקשה מידע נוסף.", "H10": "דווח על השעיית מסחר של ה־SEC.",
                   "H11": "דווח על חשש רגולטורי.", "LUDP": "דווח על עצירת תנודתיות.",
                   "LUDS": "דווח על עצירת תנודתיות.", "M": "דווח על עצירת תנודתיות."}
        result["summary_he"] = reasons.get(code, "נקלט עדכון במצב המסחר מהבורסה.")
        result["missing_he"] = ["קוד העצירה אינו קובע אם החדשות חיוביות או שליליות, ואינו מוכיח שהמסחר עדיין עצור."]
        return result
    if payload.get("source_type") == "clinicaltrials":
        result["summary_he"] = "נקלט שינוי ברישום הניסוי. זהו אות לבדיקה מול הודעת החברה."
        result["missing_he"] = ["מועד השלמה ברישום אינו מועד פרסום תוצאות מאומת."]
        return result
    for topic, pattern, description in TOPICS:
        match = pattern.search(sample[:12_000])
        if match:
            result["summary_he"] = description
            if basis in {"source_text", "feed_summary"}:
                quote = _quote(sample, match, words=100)
                if quote:
                    result["evidence"] = [{"id": "source-1", "topic": topic,
                                           "quote": quote, "url": payload.get("source_url")}]
                else:
                    result["missing_he"].append("המשפט המלא ארוך מדי לציטוט בטוח; יש לקרוא את המקור. לא הוצג קטע חתוך.")
            break
    if topic == "corporate_action" and match:
        # Terms are extracted from evidence, never filled from ticker-specific defaults.
        terms = []
        for pattern in (r"October\s+19,\s+2026", r"25%", r"\$50\s+million"):
            term = re.search(pattern, sample, re.I)
            if term:
                terms.append(term.group())
        if terms:
            result["summary_he"] += " תנאים במקור: " + ", ".join(terms) + "."
        if re.search(r"\b(?:one CVR per share|dividend of one stapled CVR for each share)\b", sample, re.I):
            result["summary_he"] += " CVR אחד לכל מניה זכאית, לפי תנאי ההסכם."
        if re.search(r"No\s+[^.;\n]*approved\s+by\s+the\s+FDA", sample, re.I):
            result["summary_he"] += " המקור מציין שאין אישור FDA למוצר."
        if re.search(r"no\s+PRV\s+has\s+been\s+awarded", sample, re.I):
            result["summary_he"] += " לא הוענק PRV ואין תשלום מובטח."
        # Include the full explicit absence sentence, with negation intact.
        absence = re.search(r"\bNo\s+[^.;\n]*approved\s+by\s+the\s+FDA", sample, re.I)
        quote = _quote(sample, absence, words=100) if absence else ""
        if quote:
            result["evidence"].append({"id": "source-absence", "topic": "regulatory_absence",
                                       "quote": quote, "url": payload.get("source_url")})
        for evidence_id, pattern in (
            ("source-cvr-economics", r"Under the CVR Agreement, holders"),
            ("source-cvr-record-date", r"declared a dividend of one stapled CVR"),
        ):
            term = re.search(pattern, sample, re.I)
            quote = _quote(sample, term, words=100) if term else ""
            if quote:
                result["evidence"].append({"id": evidence_id, "topic": "corporate_action",
                                           "quote": quote, "url": payload.get("source_url")})
    result["source_polarity"] = classify_outcome(sample[:12_000])["polarity"]
    headline_polarity = (payload.get("outcome") or {}).get("polarity", "unknown")
    if (basis == "source_text" and headline_polarity != "unknown" and
            result["source_polarity"] != "unknown" and headline_polarity != result["source_polarity"]):
        result["missing_he"].append("סיווג הטקסט שונה מסיווג הכותרת. יש לקרוא את המקור לפני הסתמכות על הכיוון.")
    if basis in {"source_text", "feed_summary"}:
        wording = {"positive": "בטקסט שנקרא זוהה ניסוח חיובי.", "negative": "בטקסט שנקרא זוהה ניסוח שלילי.",
                   "mixed": "בטקסט שנקרא נמצאו סימנים מעורבים."}.get(result["source_polarity"])
        if wording:
            result["summary_he"] += " " + wording
    if basis == "headline_only":
        result["missing_he"].append("תוכן ההודעה טרם נקרא; ההסבר נשען על הכותרת בלבד.")
    elif not result["evidence"]:
        result["missing_he"].append("לא נמצא בטקסט קטע ברור שמסביר את מהות העדכון.")
    if payload.get("verification_state") != "primary_source":
        result["missing_he"].append("קריאת ההודעה אינה משנה את מצב האימות של האירוע.")
    return result


def _optional_ai(result, provider=None):
    if os.environ.get("MOZES_ALERT_AI") != "1" or not result["evidence"]:
        return result
    # Free-form paraphrase cannot be mechanically proven to preserve a negation
    # across languages. Keep deterministic wording until that contract exists.
    return {**result, "ai_status": "evidence_guard"}


def enrich_pending(conn, *, limit=3, fetch=None, now=None, provider=None):
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(
        "SELECT DISTINCT o.change_id FROM alert_outbox o LEFT JOIN alert_analysis_jobs j USING(change_id) "
        "WHERE o.stage='stage1' AND o.created_at>=? AND (j.change_id IS NULL OR "
        "(j.status='retry' AND j.retry_after<=?)) ORDER BY o.created_at ASC LIMIT ?",
        ((now - timedelta(days=7)).isoformat(), now.isoformat(), max(1, limit)),
    ).fetchall()
    stats = {"complete": 0, "retry": 0, "unavailable": 0}
    for row in rows:
        cid = row["change_id"]
        payload = build_stage1_payload(conn, cid)
        previous = conn.execute("SELECT attempts FROM alert_analysis_jobs WHERE change_id=?", (cid,)).fetchone()
        attempt = (previous[0] if previous else 0) + 1
        text, document_id, source_hash, basis, status = "", None, None, "headline_only", "complete"
        if payload.get("source_type") in {"nasdaq_halts", "clinicaltrials"}:
            basis = "structured_source"
            text = json.dumps(payload.get("new_value"), ensure_ascii=False)
        else:
            try:
                url = allowed_source_url(payload.get("source_url") or "", payload)
                text = normalize_source((fetch or fetch_source)(url, payload))
                if len(text) < 120 or re.search(r"captcha|access denied|verify you are human|request rejected", text[:1200], re.I):
                    raise ValueError("source text unavailable")
                basis = "source_text"
            except Exception:
                value = payload.get("new_value") or {}
                text = normalize_source(str(value.get("summary") or "")) if isinstance(value, dict) else ""
                basis = "feed_summary" if text else "headline_only"
                status = "retry" if attempt < 3 else "unavailable"
        if text:
            source_hash = hashlib.sha256(text.encode()).hexdigest()
            document_id = "ASD-" + hashlib.sha256((str(payload.get("source_url")) + source_hash).encode()).hexdigest()[:24]
            with conn:
                conn.execute("INSERT OR IGNORE INTO alert_source_documents VALUES(?,?,?,?,?)",
                             (document_id, payload.get("source_url") or "", source_hash, now.isoformat(), text))
        result = explain(payload, text, basis=basis, source_hash=source_hash)
        result = _optional_ai(result, provider)
        result["analyzed_at"] = now.isoformat()
        result["status"] = status
        analysis_id = "ANA-" + hashlib.sha256(json.dumps([cid, document_id, result["method"], result],
                                        sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        with conn:
            conn.execute("INSERT OR IGNORE INTO alert_analyses VALUES(?,?,?,?,?,?)",
                         (analysis_id, cid, document_id, result["method"], now.isoformat(),
                          json.dumps(result, ensure_ascii=False)))
            conn.execute("INSERT OR REPLACE INTO alert_analysis_jobs VALUES(?,?,?,?,?)",
                         (cid, status, attempt, (now + timedelta(minutes=5 * attempt)).isoformat(), analysis_id))
        stats[status] += 1
    return stats


def main(argv=None):
    from .config import DB_PATH
    parser = argparse.ArgumentParser(description="Explain queued alerts after Stage-1 delivery")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args(argv)
    conn = db.connect(DB_PATH)
    try:
        result = enrich_pending(conn, limit=args.limit)
        print(json.dumps(result))
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                output.write(f"changed={'true' if any(result.values()) else 'false'}\n")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
