"""Deterministic milestone timing. A trigger is never a calendar prediction."""
from __future__ import annotations

import re
from datetime import datetime

COUNT = re.compile(r"(?:after|following|upon|reaching|required|specified|target)\s+(?:the\s+)?(?:pre-specified\s+)?(\d+)\s*(?:st|nd|rd|th)?\s+(events?|deaths?)", re.I)
PROGRESS = re.compile(r"\b(\d+)\s+(?:events|deaths)\s+(?:have\s+)?(?:occurred|reported|confirmed|observed)", re.I)
MILESTONES = (
    ("ENROLLMENT_COMPLETE", r"(?:once|after|upon|following)\s+(?:the\s+)?enrollment\s+(?:is\s+)?(?:complete|completion)"),
    ("DATABASE_LOCK", r"(?:after|upon|following)\s+(?:the\s+)?database lock"),
    ("DSMB_REVIEW", r"(?:after|upon|following)\s+(?:the\s+)?(?:DSMB|IDMC)\s+(?:review|threshold)"),
    ("FINAL_ANALYSIS", r"(?:after|upon|following)\s+(?:the\s+)?final analysis"),
    ("EVENT_COUNT", r"(?:after|upon|following)\s+(?:the\s+)?target event count"),
)


def parse_trigger(quote):
    match = COUNT.search(quote)
    target = int(match[1]) if match else None
    if target == 0:
        return None
    kind = ("DEATH_COUNT" if match and match[2].lower().startswith("death") else "EVENT_COUNT") if match else None
    if not kind:
        kind = next((code for code, pattern in MILESTONES if re.search(pattern, quote, re.I)), None)
    if not kind:
        return None
    current_match = PROGRESS.search(quote)
    current = int(current_match[1]) if current_match else None
    progress_date = re.search(r"as of ([A-Za-z]+ \d{1,2}, \d{4})", quote, re.I)
    as_of = None
    if progress_date:
        try:
            as_of = datetime.strptime(progress_date[1], "%B %d, %Y").date().isoformat()
        except ValueError:
            pass
    state = "UNKNOWN_PROGRESS"
    if target is not None:
        state = "AWAITING_TRIGGER"
        if current is not None:
            state = "TRIGGER_REACHED" if current >= target else "NEAR_TRIGGER" if current / target >= .9 else state
    return {"timing_mode": "EVENT_DRIVEN", "trigger_type": kind,
            "trigger_target": target, "trigger_current": current,
            "trigger_precision": "EXACT_COUNT" if target is not None else "MILESTONE",
            "monitoring_state": state, "trigger_quote": quote, "trigger_as_of": as_of}
