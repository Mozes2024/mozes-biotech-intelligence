import subprocess
from datetime import datetime, timezone
from pathlib import Path

from mozes import db
from mozes import live_monitor
from mozes.live_monitor import recent_changes, record_change
from mozes.alert_dispatch import enqueue_change, recent_alerts


def test_recent_visibility_browser_contract():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', '--test', 'tests/test_recent_visibility.mjs'], cwd=root,
                            capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr


def test_oct13_records_remain_visible_in_oct14_seven_day_queries(tmp_path, monkeypatch):
    monkeypatch.delenv('MOZES_SMTP_USER', raising=False)
    at = '2026-10-13T12:00:00+00:00'
    monkeypatch.setattr(live_monitor, '_now', lambda: at)
    monkeypatch.setattr(db, 'utcnow', lambda: at)
    conn = db.connect(tmp_path / 'test.db')
    change = record_change(conn, ticker='TEST', change_type='company_release_signal',
                           severity='high', previous_value=None, new_value={'headline': 'Source update'},
                           source_url='https://example.test/release', source_type='company_ir')
    enqueue_change(conn, change, channels=('log',))
    now = datetime(2026, 10, 14, 9, tzinfo=timezone.utc)
    before = conn.total_changes
    assert change in {row['change_id'] for row in recent_changes(conn, now=now)}
    assert change in {row['change_id'] for row in recent_alerts(conn, now=now)}
    assert conn.total_changes == before
