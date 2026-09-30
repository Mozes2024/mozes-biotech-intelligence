"""Point-in-time snapshots. The ONLY path from raw event data into scoring.

Guarantees (enforced + tested in tests/test_leakage.py):
  * documents / analyst items / flags / chronology with effective date >= as_of are excluded
  * prices on or after as_of are excluded (as_of = catalyst date -> last usable close is T-1)
  * trial-registry versions: latest version strictly before as_of
  * features are used only if features_as_of < as_of
  * items without a known date are EXCLUDED (conservative)
  * events carrying outcome fields are rejected
  * snapshots are deeply immutable
"""
from __future__ import annotations

import copy
from types import MappingProxyType

from .dates import pub_effective

OUTCOME_KEYS = frozenset({"outcome", "outcomes", "clinical_outcome", "move", "move_basis", "result_label", "actual_move"})


class LeakageError(AssertionError):
    pass


def all_keys(obj):
    keys = set()
    if isinstance(obj, (dict, MappingProxyType)):
        for k, v in obj.items():
            keys.add(k)
            keys |= all_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            keys |= all_keys(v)
    return keys


def freeze(o):
    if isinstance(o, dict):
        return MappingProxyType({k: freeze(v) for k, v in o.items()})
    if isinstance(o, list):
        return tuple(freeze(v) for v in o)
    return o


def thaw(o):
    if isinstance(o, (dict, MappingProxyType)):
        return {k: thaw(v) for k, v in o.items()}
    if isinstance(o, tuple):
        return [thaw(v) for v in o]
    return o


def before(d, as_of: str) -> bool:
    return pub_effective(d) < as_of


def pick_version(versions, as_of):
    vs = sorted((v for v in versions if before(v.get("date"), as_of)), key=lambda v: pub_effective(v["date"]))
    return vs[-1] if vs else None


def snapshot_at(event: dict, as_of: str):
    leaked = all_keys(event) & OUTCOME_KEYS
    if leaked:
        raise LeakageError(f"event {event.get('id')} carries outcome fields: {sorted(leaked)}")
    runway = event.get("runway")
    feats_ok = bool(event.get("features_as_of")) and before(event["features_as_of"], as_of)
    snap = {
        "id": event.get("id"), "ticker": event.get("ticker"), "company": event.get("company"),
        "program": event.get("program"), "indication": event.get("indication"),
        "ta": event.get("ta"), "type": event.get("type"), "phase": event.get("phase"),
        "pivotal": bool(event.get("pivotal")), "mcap": event.get("mcap") or "unknown",
        "dependency": event.get("dependency"), "commercial": bool(event.get("commercial")),
        "enables_filing": bool(event.get("enables_filing")),
        "runway_months": runway.get("months") if runway and before(runway.get("known_from"), as_of) else None,
        "as_of": as_of,
        "documents": [d for d in event.get("documents", []) if before(d.get("published"), as_of)],
        "prices": sorted((p for p in event.get("prices", []) if p["date"] < as_of), key=lambda p: p["date"]),
        "bench": sorted((p for p in event.get("bench", []) if p["date"] < as_of), key=lambda p: p["date"]),
        "analyst": [a for a in event.get("analyst", []) if before(a.get("date"), as_of)],
        "trial_record": pick_version(event.get("trial_versions", []), as_of),
        "features": dict(event.get("features") or {}) if feats_ok else {},
        "flags": [f for f in event.get("flags", []) if before(f.get("known_from"), as_of)],
        "chronology": [c for c in event.get("chronology", []) if before(c.get("date"), as_of)],
    }
    snap = copy.deepcopy(snap)
    assert_point_in_time(snap)
    return freeze(snap)


def assert_point_in_time(snap):
    """Defense in depth: re-validates a (possibly modified) snapshot. Raises LeakageError."""
    a = snap["as_of"]
    for coll, key in (("documents", "published"), ("analyst", "date"), ("chronology", "date"), ("flags", "known_from")):
        for item in snap.get(coll, ()):
            if not before(item.get(key), a):
                raise LeakageError(f"{coll} item dated {item.get(key)} is not before as_of {a}")
    for coll in ("prices", "bench"):
        for p in snap.get(coll, ()):
            if p["date"] >= a:
                raise LeakageError(f"{coll} row {p['date']} is not before as_of {a}")
    tr = snap.get("trial_record")
    if tr and not before(tr.get("date"), a):
        raise LeakageError("trial record version is not before as_of")
    leaked = all_keys(snap) & OUTCOME_KEYS
    if leaked:
        raise LeakageError(f"snapshot contains outcome fields: {sorted(leaked)}")
