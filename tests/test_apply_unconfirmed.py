"""2026-11-09, the owner's first daily auto-apply: Seatpicl and Tavily were tried
three times in one day, and Forter's confirmation email arrived for a job the
run had marked as a failure. An application that may have gone out is never
retried automatically - a person checks."""
import apply_engine as ae
from tests.test_routes import stack, users  # noqa: F401


def test_greenhouse_eu_boards_are_greenhouse():
    assert ae._is_greenhouse("https://job-boards.eu.greenhouse.io/tavily/jobs/4971192101?gh_jid=4971192101")
    assert ae._is_greenhouse("https://job-boards.greenhouse.io/figma/jobs/1")
    assert ae._is_greenhouse("https://boards.greenhouse.io/forter/jobs/8878373002")
    assert not ae._is_greenhouse("https://www.rapyd.net/company/careers/positions/x")


def test_after_submit_uncertainty_is_unconfirmed_and_never_retried():
    for err in ("Comeet: no confirmation after submit - needs manual review",
                "No confirmation after submit (form still present) - needs manual review [anti-bot widget: hcaptcha]",
                "Stopped waiting after 150s - it may have been sent. Check the company's site"):
        assert ae._classify_failure(err)[0] == "unconfirmed", err
    assert ae.retry_decision("unconfirmed", 1)[0] == "manual"
    # Before anything was sent, a slow page is still an ordinary retry.
    assert ae._classify_failure("Page load timed out")[0] == "timeout"


def test_the_deadline_result_is_held_for_a_person(stack, monkeypatch):
    app = stack["app"]
    import time as _t
    monkeypatch.setattr(app, "_APPLY_DEADLINE_S", 0)
    monkeypatch.setattr(ae, "submit_application", lambda *a, **k: _t.sleep(3) or {"status": "confirmed"})
    res = app._submit_application_guarded("https://x.example/job", "PM", "Acme", {}, None)
    assert res["apply_failure_type"] == "unconfirmed"
    assert "may have been sent" in res["error"]
    assert app._apply_failure_fields(res["apply_failure_type"], 1, res["status"]) == ("manual_required", None)


def test_migration_moves_pending_unsafe_retries_to_manual(stack, users):
    import migrations
    conn = stack["db"].get_db()
    uid = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()["id"]
    rows = {}
    for key, err in (("deadline", "Apply exceeded 150s deadline"),
                     ("nosubmit", "No confirmation after submit (form still present) - needs manual review"),
                     ("pageload", "Page load timed out")):
        conn.execute("INSERT INTO jobs (user_id, title, company, url, status, apply_status, apply_error, "
                     "apply_failure_type, apply_next_attempt_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (uid, "PM " + key, "Acme", "https://acme.example/" + key, "approved", "failed", err,
                      "timeout", "2026-11-09T13:20:00"))
        rows[key] = conn.execute("SELECT MAX(id) AS m FROM jobs").fetchone()["m"]
    conn.commit()
    migrations.m0027_unconfirmed_applies(conn)
    got = {k: dict(conn.execute("SELECT apply_status, apply_next_attempt_at FROM jobs WHERE id=?", (i,)).fetchone())
           for k, i in rows.items()}
    conn.close()
    assert got["deadline"] == {"apply_status": "manual_required", "apply_next_attempt_at": None}
    assert got["nosubmit"]["apply_status"] == "manual_required"
    assert got["pageload"]["apply_status"] == "failed"        # never sent: still retried
