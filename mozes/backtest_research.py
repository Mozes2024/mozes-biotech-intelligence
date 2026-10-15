"""Read-only research diagnostics; never a strategy executor or gate evaluator."""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone

from . import db
from .historical import market_event_date


DEFAULT_COSTS = (
    {'name': 'frictionless_reference', 'commission_bps': 0, 'half_spread_bps': 0, 'slippage_bps': 0},
    {'name': 'illustrative_25bps_per_side', 'commission_bps': 0, 'half_spread_bps': 10, 'slippage_bps': 15},
    {'name': 'stress_100bps_per_side', 'commission_bps': 5, 'half_spread_bps': 35, 'slippage_bps': 60},
)


def cost_sensitivity(gross_return, scenarios=DEFAULT_COSTS):
    """Long-only proportional round trip. Assumptions, NOT historical quotes/fills.

    Entry costs raise cash invested, exit costs reduce proceeds. No claim about
    actual spreads, liquidity, overnight fills, impact, borrow or commissions.
    """
    if not math.isfinite(gross_return) or gross_return <= -1:
        raise ValueError('invalid gross return')
    out = []
    for scenario in scenarios:
        values = [scenario[k] for k in ('commission_bps', 'half_spread_bps', 'slippage_bps')]
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or
               not math.isfinite(x) or x < 0 for x in values) or sum(values) >= 10000:
            raise ValueError('invalid cost assumption')
        per_side = sum(values) / 10000
        out.append({**scenario, 'assumptions_only': True, 'gross_return': gross_return,
                    'net_return': (1 + gross_return) * (1 - per_side) / (1 + per_side) - 1})
    return out


def _aware(value):
    if not isinstance(value, str) or 'T' not in value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo is not None else None
    except ValueError:
        return None


def entry_audit(conn, case, entry_date):
    """Conservative pre-entry diagnostic, not certification of a trading rule.

    A date-only decision is bounded at UTC midnight; same-day sources/snapshots
    cannot justify a close execution. Later retrieved/revised evidence cannot
    establish the original source version. No free-text schedule is inferred.
    """
    reasons = ['provider_adjustment_provenance_unverified_META_M8',
               'execution_session_and_fills_unverified', 'prospective_strategy_not_registered']
    cutoff = None
    try:
        if date.fromisoformat(entry_date).isoformat() == entry_date:
            cutoff = datetime.combine(date.fromisoformat(entry_date), datetime.min.time(), timezone.utc)
    except (TypeError, ValueError):
        pass
    candidates = []
    for snap in db.feature_snapshot_rows(conn, case['case_id']):
        at = _aware(snap['as_of'])
        if not cutoff or not at or at >= cutoff or not snap['blinded']:
            continue
        sources = json.loads(snap['provenance_json'] or '[]')
        archived = [db.source_archive_row(conn, source_id) for source_id in sources]
        if not archived or any(not s or not s['content_hash'] or not _aware(s['published_at']) or
                               not _aware(s['retrieved_at']) or
                               _aware(s['published_at']) > at or _aware(s['retrieved_at']) > at for s in archived):
            continue
        candidates.append((at, snap))
    known = None
    if candidates:
        payload = json.loads(max(candidates, key=lambda row: row[0])[1]['payload'])
        value = payload.get('expected_event_date')
        try:
            if isinstance(value, str) and date.fromisoformat(value).isoformat() == value:
                known = value
        except ValueError:
            pass
    else:
        reasons.append('no_captured_blinded_snapshot_before_entry')
    # Typed snapshot date is diagnostic only: no literal claim/source binding
    # or complete revision history is certified by the current schema.
    reasons.append('schedule_source_and_revision_history_not_certified')
    if known is None:
        reasons.append('schedule_not_proven_at_entry')
    elif known != market_event_date(case['event_at']).isoformat():
        reasons.append('final_date_differs_from_entry_forecast')
    return {'case_id': case['case_id'], 'entry_date': entry_date,
            'entry_snapshot_available': bool(candidates), 'known_schedule_date': known,
            'eligible': False, 'reasons': reasons}
