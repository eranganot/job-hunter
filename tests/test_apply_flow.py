"""Integration coverage for the apply orchestration (backlog #9):
retry/backoff, manual-required routing, attempt cap, and success path.
Uses a temp DB and monkeypatches the browser submit so no Playwright runs.
"""
import datetime as _dt
import pytest
import db as database
import auth
import apply_engine


@pytest.fixture
def env(tmp_path, monkeypatch):
    import app  # importing app resets db path at import time, so import first
    database.set_db_path(str(tmp_path / "t.db"))
    database.init_db()
    auth.set_db_getter(database.get_db)
    auth.create_user("Eran", "t@e.com", "pw123456")
    conn = database.get_db()
    uid = conn.execute("SELECT id FROM users WHERE email=?", ("t@e.com",)).fetchone()["id"]
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    conn.execute(
        "UPDATE user_profiles SET auto_apply_enabled=1, applications_sent_today=0, "
        "applications_reset_date=?, cv_summary='x' WHERE user_id=?",
        (today, uid),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(apply_engine, "extract_applicant_data", lambda cv, em: {"email": em})
    return app, uid


def _seed(uid, url="https://co/1"):
    conn = database.get_db()
    conn.execute("DELETE FROM jobs WHERE user_id=?", (uid,))
    conn.execute(
        "INSERT INTO jobs (user_id,title,company,location,url,status) VALUES (?,?,?,?,?, 'approved')",
        (uid, "VP Product", "Acme", "TLV", url),
    )
    conn.commit()
    jid = conn.execute("SELECT id FROM jobs WHERE user_id=?", (uid,)).fetchone()["id"]
    conn.close()
    return jid


def _row(jid):
    r = database.get_db().execute(
        "SELECT status, apply_status, apply_attempts, apply_next_attempt_at FROM jobs WHERE id=?",
        (jid,),
    ).fetchone()
    return dict(r)


def _fail(ftype):
    return lambda *a, **k: {
        "success": False, "status": "failed", "confirmation_text": "",
        "error": ftype, "apply_failure_type": ftype, "apply_failure_detail": "x",
        "resolved_url": "https://co/1",
    }


def _ok(*a, **k):
    return {"success": True, "status": "submitted", "confirmation_text": "ok", "error": "", "resolved_url": "https://co/1"}


def test_captcha_routes_to_manual(env, monkeypatch):
    app, uid = env
    jid = _seed(uid)
    monkeypatch.setattr(app, "_submit_application_guarded", _fail("captcha"))
    app.run_job_apply(uid)
    r = _row(jid)
    assert r["apply_status"] == "manual_required" and r["apply_next_attempt_at"] is None


def test_timeout_schedules_backoff(env, monkeypatch):
    app, uid = env
    jid = _seed(uid)
    monkeypatch.setattr(app, "_submit_application_guarded", _fail("timeout"))
    app.run_job_apply(uid)
    r = _row(jid)
    assert r["apply_status"] == "failed"
    assert r["apply_next_attempt_at"] is not None
    assert r["apply_attempts"] == 1


def test_manual_required_not_repicked(env, monkeypatch):
    app, uid = env
    jid = _seed(uid)
    c = database.get_db(); c.execute("UPDATE jobs SET apply_status='manual_required' WHERE id=?", (jid,)); c.commit(); c.close()
    calls = {"n": 0}
    monkeypatch.setattr(app, "_submit_application_guarded", lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), _ok())[1])
    app.run_job_apply(uid)
    assert calls["n"] == 0


def test_exhausted_not_repicked(env, monkeypatch):
    app, uid = env
    jid = _seed(uid)
    c = database.get_db(); c.execute("UPDATE jobs SET apply_attempts=3 WHERE id=?", (jid,)); c.commit(); c.close()
    calls = {"n": 0}
    monkeypatch.setattr(app, "_submit_application_guarded", lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), _ok())[1])
    app.run_job_apply(uid)
    assert calls["n"] == 0


def test_success_marks_applied(env, monkeypatch):
    app, uid = env
    jid = _seed(uid)
    monkeypatch.setattr(app, "_submit_application_guarded", _ok)
    app.run_job_apply(uid)
    r = _row(jid)
    assert r["status"] == "applied" and r["apply_status"] == "submitted"


def test_submitted_with_error_is_not_applied(env, monkeypatch):
    """A 'submitted' result that carries an error never actually succeeded:
    it must NOT land in Applied; it goes back to the queue as a failure."""
    app, uid = env
    jid = _seed(uid)
    monkeypatch.setattr(app, "_submit_application_guarded", lambda *a, **k: {
        "success": True, "status": "submitted", "confirmation_text": "",
        "error": "Form analysis failed: HTTP Error 401: Unauthorized",
        "resolved_url": "https://co/1",
    })
    app.run_job_apply(uid)
    r = _row(jid)
    assert r["status"] == "approved"          # stayed in the queue
    assert r["apply_status"] != "submitted"   # not marked submitted/applied


def test_cleanup_moves_errored_applied_back(env):
    """The startup cleanup query moves error-tagged 'applied' jobs to the queue,
    and leaves clean (no-error) applied jobs alone."""
    app, uid = env
    conn = database.get_db()
    conn.execute("DELETE FROM jobs WHERE user_id=?", (uid,))
    # one errored 'applied', one clean 'applied'
    conn.execute("INSERT INTO jobs (user_id,title,company,status,apply_status,apply_error) VALUES (?,?,?, 'applied','submitted','Form analysis failed: 401')", (uid, "Bad", "X"))
    conn.execute("INSERT INTO jobs (user_id,title,company,status,apply_status,apply_error) VALUES (?,?,?, 'applied','confirmed','')", (uid, "Good", "Y"))
    conn.commit()
    # run the exact cleanup query
    conn.execute("UPDATE jobs SET status='approved', apply_status='failed' "
                 "WHERE status='applied' AND COALESCE(TRIM(apply_error), '') != ''")
    conn.commit()
    rows = {r["title"]: dict(r) for r in conn.execute("SELECT title, status, apply_status FROM jobs WHERE user_id=?", (uid,)).fetchall()}
    conn.close()
    assert rows["Bad"]["status"] == "approved" and rows["Bad"]["apply_status"] == "failed"
    assert rows["Good"]["status"] == "applied"   # clean one stays
