"""C4 + C7 (2026-11-24): owner alerts fire once at the right thresholds."""
import uuid

from tests.test_routes import stack  # noqa: F401
import opsalerts


def _conn(stack):
    return stack["db"].get_db()


def _usage(calls, cap, tokens=0):
    return {"global": {"calls": calls, "tokens": tokens}, "limits": {"global_calls": cap}}


def test_ai_steps_fire_once(stack):
    conn = _conn(stack)
    try:
        conn.execute("DELETE FROM app_flags WHERE key LIKE 'ai_%'"); conn.commit()
        sent = []
        assert opsalerts.run(conn, _usage(400, 1000), sent.append) == []
        keys = opsalerts.run(conn, _usage(500, 1000, 2_000_000), sent.append)
        assert [k.split(":")[0] for k in keys] == ["ai_50"] and "50%" in sent[0] and "$1" in sent[0]
        assert opsalerts.run(conn, _usage(600, 1000), sent.append) == []        # not twice
        keys = opsalerts.run(conn, _usage(900, 1000), sent.append)
        assert [k.split(":")[0] for k in keys] == ["ai_80"]
        assert opsalerts.run(conn, _usage(900, 0), sent.append) == []          # no ceiling, no alert
    finally:
        conn.close()


def test_failed_send_is_retried(stack):
    conn = _conn(stack)
    try:
        conn.execute("DELETE FROM app_flags WHERE key LIKE 'ai_%'"); conn.commit()
        def boom(m):
            raise RuntimeError("down")
        assert opsalerts.run(conn, _usage(500, 1000), boom) == []
        assert len(opsalerts.run(conn, _usage(500, 1000), lambda m: None)) == 1
    finally:
        conn.close()


def test_users_milestone_counts_real_users_only(stack, monkeypatch):
    import auth
    conn = _conn(stack)
    try:
        base = opsalerts.real_users(conn)
        auth.create_user("T", "x-%s@job-hunter.test" % uuid.uuid4().hex[:6], "password123")
        assert opsalerts.real_users(conn) == base
        monkeypatch.setattr(opsalerts, "USERS_MILESTONE", base + 1)
        assert not [k for k, _ in opsalerts.due(conn, {}) if k.startswith("users_")]
        auth.create_user("R", "r-%s@example.com" % uuid.uuid4().hex[:6], "password123")
        msgs = [m for k, m in opsalerts.due(conn, {}) if k.startswith("users_")]
        assert msgs and "Resend" in msgs[0]
    finally:
        conn.close()


def test_email_count_and_warning(stack, monkeypatch):
    conn = _conn(stack)
    try:
        start = opsalerts.emails_today(conn)
        assert opsalerts.count_email(conn) == start + 1
        monkeypatch.setattr(opsalerts, "EMAILS_WARN", start + 1)
        assert [m for k, m in opsalerts.due(conn, {}) if k.startswith("emails_")]
    finally:
        conn.close()


def test_waitlist_alert(stack, monkeypatch):
    import auth
    monkeypatch.setenv("JH_SIGNUPS", "paused")
    auth.create_user("W", "w-%s@example.com" % uuid.uuid4().hex[:6], "password123")
    conn = _conn(stack)
    try:
        assert [m for k, m in opsalerts.due(conn, {}) if k.startswith("waitlist:")]
    finally:
        conn.close()


def test_disk_alert_once_a_day(stack):
    conn = _conn(stack)
    try:
        conn.execute("DELETE FROM app_flags WHERE key LIKE 'disk_%'"); conn.commit()
        disk = {"postgres": {"used_mb": 3800, "cap_mb": 5120, "pct": 74}, "volume": {"used_mb": 10, "cap_mb": 5120, "pct": 0}}
        due = opsalerts.disk_due(conn, disk)
        assert [k.split(":")[0] for k, _ in due] == ["disk_postgres"] and "74%" in due[0][1]
        opsalerts._mark(conn, due[0][0])
        assert opsalerts.disk_due(conn, disk) == []
    finally:
        conn.close()


def test_evening_report(stack):
    conn = _conn(stack)
    try:
        conn.execute("DELETE FROM app_flags WHERE key LIKE 'evening:%'"); conn.commit()
        assert opsalerts.evening_due(conn, 20)[0] is False
        due, key = opsalerts.evening_due(conn, 21)
        assert due
        kpi = {"kpis": {"activation_48h": {"value": 40, "n": 2, "of": 5},
                        "weekly_active_swipers": {"value": 7}, "first_applications": {"value": 3}}}
        msg = opsalerts.evening_report(conn, _usage(1200, 15000, 12_000_000), kpi,
                                       {"postgres": {"pct": 12}})
        for part in ("New sign-ups", "1,200 of 15,000", "Emails sent", "activation 40%", "Disk postgres: 12%"):
            assert part in msg, part
        opsalerts._mark(conn, key)
        assert opsalerts.evening_due(conn, 22)[0] is False
    finally:
        conn.close()


def test_disk_status_shape(stack):
    d = stack["app"]._disk_status()
    assert "volume" in d and d["volume"]["cap_mb"] > 0


def test_evening_report_with_no_sign_ups_old_enough(stack):
    """24 Nov report read "activation None% (0 of 0)": activation counts only
    sign-ups at least 48 hours old, and there were none yet."""
    conn = _conn(stack)
    try:
        kpi = {"kpis": {"activation_48h": {"value": None, "n": 0, "of": 0},
                        "weekly_active_swipers": {"value": 2}, "first_applications": {"value": 0}}}
        msg = opsalerts.evening_report(conn, _usage(985, 15000, 1_000_000), kpi, {})
        assert "None%" not in msg and "no sign-ups 2+ days old yet" in msg
    finally:
        conn.close()
