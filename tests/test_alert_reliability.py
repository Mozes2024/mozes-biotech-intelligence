"""Delivery and recovery contracts; all transports stay offline."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from mozes import alert_dispatch, db
from mozes.live_monitor import record_change


@pytest.fixture(autouse=True)
def no_push(monkeypatch):
    for key in ("MOZES_NTFY_URL", "MOZES_WEBHOOK_URL", "MOZES_SMTP_USER",
                "MOZES_SMTP_PASSWORD", "GITHUB_ACTIONS", "MOZES_ALERT_MIN_PRIORITY"):
        monkeypatch.delenv(key, raising=False)


def change(conn, identity="one", headline="Genmab announces positive topline results"):
    return record_change(conn, ticker="GMAB", change_type="company_release_signal",
                         previous_value=None, new_value={"headline": headline}, severity="high",
                         source_url="https://ir.genmab.com/news/" + identity,
                         source_type="company_ir", identity=[identity])


def test_next_monitor_cycle_recovers_an_earlier_enqueue_failure(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "recovery.db")
    original = alert_dispatch.enqueue_from_change_row
    monkeypatch.setattr(alert_dispatch, "enqueue_from_change_row",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("temporary DB failure")))
    cid = change(conn)
    assert conn.execute("SELECT COUNT(*) FROM change_events").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM alert_outbox").fetchone()[0] == 0
    monkeypatch.setattr(alert_dispatch, "enqueue_from_change_row", original)
    later = datetime.now(timezone.utc) + timedelta(seconds=1)
    result = alert_dispatch.sync_new_changes(conn, since_iso=later.isoformat())
    assert result["queued"] == 1
    assert conn.execute("SELECT change_id FROM alert_outbox").fetchone()[0] == cid
    assert alert_dispatch.sync_new_changes(conn, since_iso=later.isoformat())["queued"] == 0


def test_filtered_signals_do_not_starve_recovery(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "filter.db")
    original = alert_dispatch.enqueue_from_change_row
    monkeypatch.setattr(alert_dispatch, "enqueue_from_change_row",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("temporary failure")))
    cid = change(conn, "material")
    monkeypatch.setattr(alert_dispatch, "enqueue_from_change_row", original)
    for i in range(5):
        change(conn, str(i), "Genmab hosts investor day")
    result = alert_dispatch.sync_new_changes(conn, limit=1)
    assert result["queued"] == 1
    assert conn.execute("SELECT change_id FROM alert_outbox").fetchone()[0] == cid


def test_log_receipt_is_not_email_delivery_and_channels_stay_separate(tmp_path):
    conn = db.connect(tmp_path / "channels.db")
    cid = change(conn)
    alert_dispatch.dispatch_pending(conn, now=datetime.now(timezone.utc) + timedelta(seconds=1))
    row = alert_dispatch.recent_alerts(conn)[0]
    assert row["delivered"] is False
    assert row["delivery"]["log"]["status"] == "sent"
    assert "email" not in row["delivery"]
    alert_dispatch.enqueue_change(conn, cid, channels=("email", "ntfy"))
    def fail(_):
        raise OSError("smtp unavailable")
    alert_dispatch.dispatch_pending(conn, now=datetime.now(timezone.utc) + timedelta(seconds=1),
                                    sender={"email": fail, "ntfy": lambda _: {"provider_message_id": "n1"}})
    row = alert_dispatch.recent_alerts(conn)[0]
    assert row["delivered"] is True
    assert row["delivery"]["email"]["status"] == "failed"
    assert row["delivery"]["ntfy"]["status"] == "sent"
    assert "smtp unavailable" not in json.dumps(row)


def test_one_enqueue_failure_does_not_stop_other_missing_changes(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / 'isolated.db')
    original = alert_dispatch.enqueue_from_change_row
    monkeypatch.setattr(alert_dispatch, 'enqueue_from_change_row',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('temporary failure')))
    failed = change(conn, 'first', 'Genmab positive Phase 3 topline')
    recovered = change(conn, 'second', 'Genmab receives FDA approval of new therapy')
    def enqueue(conn, **kwargs):
        if kwargs['change_id'] == failed:
            raise ValueError('private details must not leak')
        return original(conn, **kwargs)
    monkeypatch.setattr(alert_dispatch, 'enqueue_from_change_row', enqueue)
    result = alert_dispatch.sync_new_changes(conn)
    assert result['queued'] == 1
    assert result['errors'] == [{'change_id':failed,'error':'ValueError'}]
    assert conn.execute('SELECT change_id FROM alert_outbox').fetchone()[0] == recovered
