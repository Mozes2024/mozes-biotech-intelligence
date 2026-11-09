"""Decision dates for FDA cancer approvals, distinct from RSS republication time."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from .live_monitor import observe

_OPENING = re.compile(
    r'^On\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4}),?\s+(?:the\s+)?'
    r'(?:(?:U\.?S\.?\s+)?Food and Drug Administration|FDA)\s+'
    r'(?:granted|approved|has approved)\b', re.M | re.I)


def decision_date(text):
    match = _OPENING.search(text or '')
    if not match:
        return None
    try:
        return datetime.strptime(match[1], '%B %d, %Y').date().isoformat()
    except ValueError:
        return None


def cancer_approval(url):
    parts = urlsplit(url)
    return (parts.hostname in {'fda.gov', 'www.fda.gov'}
            and parts.path.startswith('/drugs/cancer-approvals/'))


def validate_item(conn, item, *, now, read_article, budget):
    """Return a fresh item or None; retain unresolved/historical source evidence.

    Body reads are bounded per pass and successful dates cached by URL and feed
    text. No alert/history/delivery records are changed here.
    """
    if not cancer_approval(item['url']):
        return item, 'not_applicable'
    key = 'fda_decision_date:' + hashlib.sha256(item['url'].encode()).hexdigest()[:24]
    fingerprint = hashlib.sha256((item['title'] + '\n' + item.get('summary', '')).encode()).hexdigest()
    day = decision_date(item.get('summary', ''))
    basis = 'feed_summary'
    error = None
    if not day:
        row = conn.execute('SELECT value_json FROM monitor_observations WHERE observation_key=?', (key,)).fetchone()
        prior = json.loads(row[0]) if row else {}
        if (prior.get('status') in {'fresh_decision', 'historical_decision'}
                and prior.get('feed_text_hash') == fingerprint and prior.get('decision_date')
                and prior.get('feed_published_at') == item['published_at']):
            day, basis = prior['decision_date'], 'article_body_cached'
        elif budget[0] > 0:
            budget[0] -= 1
            try:
                from .alert_explanation import normalize_source
                day = decision_date(normalize_source(read_article(item['url'])))
                basis = 'article_body'
            except Exception as exc:  # a failed date read must not abort other feed items
                error = type(exc).__name__ + ': ' + str(exc)[:120]
        else:
            error = 'article_read_budget_exhausted'
    stamp = datetime.fromisoformat(item['published_at'])
    date_at = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) if day else None
    # Day precision cannot prove a fresh event if the decision date is absent or
    # in the future. Old decisions remain historical even when RSS says today.
    if not date_at or date_at.date() > now.date():
        state = 'date_unverified'
    elif date_at < now - timedelta(hours=36) and stamp.date() != date_at.date():
        state = 'historical_decision'
    else:
        state = 'fresh_decision'
    observe(conn, key, {'status': state, 'checked_at': now.isoformat(),
            'feed_published_at': item['published_at'], 'decision_date': day,
            'date_basis': basis, 'feed_text_hash': fingerprint, 'error': error},
            source_url=item['url'], source_type='fda')
    if state != 'fresh_decision':
        return None, state
    published = stamp if stamp.date() == date_at.date() else date_at
    return {**item, 'published_at': published.isoformat(),
            'feed_published_at': item['published_at'], 'decision_date': day,
            'date_basis': basis}, state
