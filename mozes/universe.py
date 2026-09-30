"""Sponsor-to-ticker mapping with conservative confidence.

Automatic sponsor mapping is intentionally strict. A low-confidence match remains a
candidate and cannot become an actionable event until manually or primarily verified.
"""
from __future__ import annotations

import re

LEGAL = re.compile(r"\b(incorporated|inc|corp|corporation|company|co|ltd|limited|plc|llc|holdings?|therapeutics?|pharmaceuticals?|pharma|biopharma|biosciences?|sciences?)\b", re.I)
NONWORD = re.compile(r"[^a-z0-9]+")


def normalize_org(name: str | None) -> str:
    s = (name or "").lower().replace("&", " and ")
    s = LEGAL.sub(" ", s)
    s = NONWORD.sub(" ", s)
    return " ".join(s.split())


def token_similarity(a: str, b: str) -> float:
    aa, bb = set(normalize_org(a).split()), set(normalize_org(b).split())
    if not aa or not bb:
        return 0.0
    inter = len(aa & bb)
    return 2 * inter / (len(aa) + len(bb))


def best_mapping(sponsor: str, rows: list[dict]) -> dict | None:
    sn = normalize_org(sponsor)
    direct = [r for r in rows if r.get("sponsor_norm") == sn]
    if direct:
        return max(direct, key=lambda r: float(r.get("confidence", 0)))
    scored = []
    for r in rows:
        sim = token_similarity(sponsor, r.get("sponsor") or r.get("name") or "")
        if sim:
            scored.append((sim, r))
    if not scored:
        return None
    sim, r = max(scored, key=lambda x: x[0])
    if sim < 0.92:
        return None
    out = dict(r)
    out["confidence"] = min(float(out.get("confidence", 1.0)), sim)
    return out
