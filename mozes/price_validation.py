"""Numeric safety only; provider/adjustment reconciliation is a separate design."""
from datetime import date
import math


def validated_prices(rows):
    """Validate the entire batch before writing; never drop rows or fill missing volume."""
    out = []
    for row in rows:
        try:
            day = row['date']
            if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                raise ValueError('date')
            close, volume = row['close'], row.get('volume')
            if isinstance(close, bool) or isinstance(volume, bool):
                raise ValueError('boolean')
            close = float(close)
            volume = float(volume) if volume is not None else None
            if not math.isfinite(close) or close <= 0:
                raise ValueError('close')
            if volume is not None and (not math.isfinite(volume) or volume < 0):
                raise ValueError('volume')
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError('invalid_market_price: positive finite close and optional nonnegative finite volume required') from exc
        out.append({**row, 'date': day, 'close': close, 'volume': volume})
    return out
