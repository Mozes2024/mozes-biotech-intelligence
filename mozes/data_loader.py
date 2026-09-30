"""Loads the curated dataset. Outcomes live in a SEPARATE file and are only
consumed by the backtest/reporting layer and by reference classes that are
filtered to events resolved strictly before the as-of date."""
import json

from .config import DATA_DIR


def _load(name):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def load_sources():
    return {s["id"]: s for s in _load("sources.json")}


def load_live():
    return _load("live_catalysts.json")


def load_historical():
    return _load("historical_events.json")


def load_outcomes():
    return _load("historical_outcomes.json")
