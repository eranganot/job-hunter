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
