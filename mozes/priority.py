"""User-requested monitoring priority; scheduling preference, not event evidence."""
from __future__ import annotations

import json
import re
from pathlib import Path

FILE = Path(__file__).parent / "data" / "priority_issuers.json"
# SEC fair-access budget: keep hot cohort bounded but large enough for a real watchlist.
HOT_CAP = 40


def priority_tickers():
    try:
        values = json.loads(FILE.read_text(encoding="utf-8")).get("tickers", [])
    except (OSError, ValueError, TypeError):
        return ()
    if not isinstance(values, list):
        return ()
    return tuple(dict.fromkeys(t for value in values[:HOT_CAP]
                               if isinstance(value, str)
                               if (t := value.strip().upper()) and re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", t)))
