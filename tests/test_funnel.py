"""Step 10 (2026-11-09): the signup funnel - signup -> verified -> CV -> first
search -> first approve -> paywall hit - recorded once per user per step, and
reported to admins as a cohort."""
import json

import pytest

from tests.test_routes import Client, stack, users  # noqa: F401


@pytest.fixture
def quiet(stack, monkeypatch):
    monkeypatch.setattr(stack["app"], "send_email", lambda *a, **k: None)
    import ratelimit
    ratelimit.reset_all()


def _steps(db, uid):
    c = db.get_db()
    try:
        return sorted(r["step"] for r in c.execute("SELECT step FROM funnel_events WHERE user_id=?", (uid,)).fetchall())
    finally:
        c.close()


def _uid(db, email):
    c = db.get_db()
    try:
        return c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
    finally:
        c.close()


def test_a_step_counts_once(stack, quiet):
    c = Client(stack["port"])
    c.post_form("/register", {"name": "Fun Nel", "email": "funnel1@example.test",
                              "password": "pw-long-enough-1", "password2": "pw-long-enough-1"})
    uid = _uid(stack["db"], "funnel1@example.test")
    app = stack["app"]
    app._funnel(uid, "first_approve")
    app._funnel(uid, "first_approve")
    app._funnel(uid, "not-a-step")
    assert _steps(stack["db"], uid) == ["first_approve", "signup"]


def test_approving_a_job_records_first_approve(stack, users, quiet):
    db = stack["db"]
    uid = _uid(db, "alice@example.test")
    c = db.get_db()
    c.execute("INSERT INTO jobs (user_id,title,company,url,status) VALUES (?,?,?,?,?)",
              (uid, "PM", "Funnelco", "https://example.test/funnel/1", "new"))
    jid = c.execute("SELECT id FROM jobs WHERE url='https://example.test/funnel/1'").fetchone()["id"]
    c.commit(); c.close()
    st, _l, _b = users["a"].post_json("/api/jobs/%d/approve" % jid, {})
    assert st == 200
    assert "first_approve" in _steps(db, uid)


def test_a_plan_refusal_records_the_paywall(stack, quiet):
    app = stack["app"]
    c = Client(stack["port"])
    c.post_form("/register", {"name": "Pay Wall", "email": "paywall@example.test",
                              "password": "pw-long-enough-1", "password2": "pw-long-enough-1"})
    uid = _uid(stack["db"], "paywall@example.test")
    body = app.entitlements.upgrade_required("cover_letter", {"id": uid, "plan": "free"})
    assert body["code"] == "upgrade_required"
    assert "paywall" in _steps(stack["db"], uid)


def test_the_funnel_report_is_a_cohort_and_admin_only(stack, users, quiet):
    c = Client(stack["port"])
    c.post_form("/register", {"name": "Co Hort", "email": "cohort@example.test",
                              "password": "pw-long-enough-1", "password2": "pw-long-enough-1"})
    uid = _uid(stack["db"], "cohort@example.test")
    stack["app"]._funnel(uid, "cv")
    assert users["b"].get("/api/admin/funnel")[0] == 403
    st, _l, body = users["admin"].get("/api/admin/funnel?days=30")
    assert st == 200
    rep = json.loads(body)
    steps = {s["step"]: s["users"] for s in rep["steps"]}
    assert list(steps) == ["signup", "verified", "cv", "first_search", "first_approve", "paywall"]
    assert steps["signup"] >= 1 and steps["cv"] >= 1
    assert steps["cv"] <= steps["signup"], "a cohort step cannot exceed its signups"


def test_robots_keeps_the_app_out_of_search(stack):
    st, _l, body = Client(stack["port"]).get("/robots.txt")
    assert st == 200
    text = body.decode()
    assert "Disallow: /app" in text and "Disallow: /api/" in text and "Allow: /$" in text
