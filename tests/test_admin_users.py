"""
tests/test_admin_users.py - enabling and disabling accounts.

Public signup needs a way to switch an abusive account off. The endpoint
existed with no UI; building the button found that it let the admin disable
THEMSELVES, which is a one-click unrecoverable lockout: get_session_user
requires is_active=1, so the next request 302s to /login and the undo answers
401. One admin account, no second door. Proven by driving the route.
"""
import json

import pytest

from tests.test_routes import (Client, stack, users, _user_id, _new_job)  # noqa: F401


def _active(db, email):
    conn = db.get_db()
    row = conn.execute("SELECT is_active FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    return row["is_active"]


def test_an_admin_cannot_disable_themselves(stack, users):
    db = stack["db"]
    admin_id = _user_id(db, "admin@example.test")
    status, _loc, body = users["admin"].post_json(f"/api/admin/users/{admin_id}/toggle", {})
    assert status == 400, "the admin was allowed to lock themselves out"
    assert json.loads(body).get("code") == "cannot_disable_self"
    assert _active(db, "admin@example.test") == 1


def test_the_admin_is_still_usable_afterwards(stack, users):
    """The half that matters: the refusal must leave the session alive. A guard
    that returns 400 AFTER writing is not a guard."""
    db = stack["db"]
    admin_id = _user_id(db, "admin@example.test")
    users["admin"].post_json(f"/api/admin/users/{admin_id}/toggle", {})
    status, _loc, _b = users["admin"].get("/api/me")
    assert status == 200, "the admin's session died despite the refusal"


def test_disabling_another_user_works_and_reports_the_new_state(stack, users):
    db = stack["db"]
    target = _user_id(db, "bob@example.test")
    status, _loc, body = users["admin"].post_json(f"/api/admin/users/{target}/toggle", {})
    assert status == 200
    assert json.loads(body)["is_active"] == 0, "the response must say what the state now IS"
    assert _active(db, "bob@example.test") == 0

    status, _loc, body = users["admin"].post_json(f"/api/admin/users/{target}/toggle", {})
    assert json.loads(body)["is_active"] == 1
    assert _active(db, "bob@example.test") == 1


def test_a_disabled_user_cannot_use_the_app(stack, users):
    """Otherwise the switch is decorative."""
    db = stack["db"]
    target = _user_id(db, "bob@example.test")
    users["admin"].post_json(f"/api/admin/users/{target}/toggle", {})
    try:
        status, location, _b = users["b"].get("/api/me")
        assert status != 200, "a disabled account was still served"
    finally:
        users["admin"].post_json(f"/api/admin/users/{target}/toggle", {})


def test_toggling_a_user_that_does_not_exist_is_a_404(stack, users):
    """It used to answer {"success": true} for any number at all, which would
    have made a typo in the UI look like it worked."""
    status, _loc, _b = users["admin"].post_json("/api/admin/users/999999/toggle", {})
    assert status == 404


def test_a_non_admin_cannot_toggle_anyone(stack, users):
    db = stack["db"]
    target = _user_id(db, "alice@example.test")
    status, _loc, _b = users["a"].post_json(f"/api/admin/users/{target}/toggle", {})
    assert status == 403
    assert _active(db, "alice@example.test") == 1


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch):
    """/api/admin/apply-selftest launches Chromium and loads example.com with a
    20 s allowance. Once Chromium was installed where the suite runs, every
    test calling it could exceed the test client's 10 s timeout (observed
    2026-10-25 and again 2026-10-26 in test_an_admin_is_not_refused, socket
    TimeoutError). Stubbed for the whole file; the suite stays offline."""
    import apply_engine

    class _NoBrowser:
        def __enter__(self):
            raise RuntimeError("browser stubbed in tests")

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(apply_engine, "sync_playwright", lambda: _NoBrowser(), raising=False)


def test_the_selftest_probe_is_read_only_and_admin_only(stack, users):
    """The 8-week ops item. It has never been run because running it meant
    hand-crafting a request; it is about to get a button, so it gets a test."""
    status, _loc, _b = users["a"].get("/api/admin/apply-selftest")
    assert status in (302, 403), "a non-admin reached the apply diagnostics"

    status, _loc, body = users["admin"].get("/api/admin/apply-selftest")
    assert status == 200
    diag = json.loads(body)
    for key in ("playwright_importable", "apply_engine_enabled_env", "gemini_key_present"):
        assert key in diag, "the probe no longer reports %r" % key


# ── Every admin endpoint must REFUSE cleanly, not crash ─────────────────────
#
# Five call sites passed `status=403` to send_json(), whose parameter is `code`.
# Every one raised TypeError while composing the refusal, so a non-admin got a
# 500 and a traceback instead of a 403. The gate still held - the request never
# reached the sensitive code - but the app reported "I am broken" where it meant
# "you may not", and nothing had ever exercised these branches, which is exactly
# why it survived. Found 2026-09-15 by the first test to hit one.

ADMIN_GETS = [
    "/api/admin/apply-selftest",
    "/api/admin/apply-test",
    "/api/admin/queue-stats",
    "/api/admin/users",
]


@pytest.mark.parametrize("path", ADMIN_GETS)
def test_a_non_admin_is_refused_not_crashed(stack, users, path):
    status, location, _b = users["a"].get(path)
    assert status != 500, "%s answered 500 - the refusal path itself is broken" % path
    assert status in (302, 403), "%s answered %s" % (path, status)


@pytest.mark.parametrize("path", ADMIN_GETS)
def test_an_admin_is_not_refused(stack, users, path):
    """Paired: if the tests above passed because everything 403s, the endpoints
    would be uniformly broken and the guard worthless."""
    status, _loc, _b = users["admin"].get(path)
    assert status == 200, "%s refuses the admin too (%s)" % (path, status)


def test_apply_test_reports_a_missing_job_as_not_found(stack, users):
    """The same TypeError sat on this path: a bad job_id 500'd instead of 404."""
    status, _loc, _b = users["admin"].get("/api/admin/apply-test?job_id=999999")
    assert status == 404, "expected 404 for an unknown job, got %s" % status


# ── Step 8.1: the probes read the CV where it really lives ──────────────────

@pytest.fixture
def _offline_apply(monkeypatch):
    """apply-test resolves a career page and reads the CV with Gemini - both
    network calls. Stubbed so these tests stay offline and inside the client's
    10 s timeout (a full run timed out on the live lookup, 2026-11-06)."""
    import apply_engine
    monkeypatch.setattr(apply_engine, "resolve_ats_application", lambda *a, **k: None)
    monkeypatch.setattr(apply_engine, "extract_applicant_data",
                        lambda *a, **k: {"full_name": "Test Person"})


def _cold_cv(stack, email):
    """The state of a fresh container: the CV is in the database (Phase 2d),
    the cached file is gone, and user_profiles.cv_path still names it."""
    import os
    import storage
    uid = _user_id(stack["db"], email)
    storage.put(uid, b"%PDF-1.4 test cv", "cv.pdf")
    cached = storage.cache_path(uid)
    conn = stack["db"].get_db()
    conn.execute("UPDATE user_profiles SET cv_path=? WHERE user_id=?", (cached, uid))
    conn.commit()
    conn.close()
    os.remove(cached)
    return uid


def test_apply_test_finds_a_cv_that_is_only_in_the_database(stack, users, _offline_apply):
    uid = _cold_cv(stack, "admin@example.test")
    job_id = _new_job(stack["db"], uid)
    status, _loc, body = users["admin"].get("/api/admin/apply-test?job_id=%d" % job_id)
    assert status == 200
    assert json.loads(body)["cv_present"] is True, "dry run says no CV while the database holds one"


def test_a_supervised_submit_hands_the_browser_a_real_cv_file(stack, users, monkeypatch, _offline_apply):
    import os
    import apply_engine
    seen = {}

    def fake_submit(url, title, company, applicant, cv_path, *a, **k):
        seen["cv"] = cv_path
        seen["readable"] = bool(cv_path and os.path.exists(cv_path))
        return {"status": "failed", "success": False, "error": "stub"}
    monkeypatch.setattr(apply_engine, "submit_application", fake_submit)
    uid = _cold_cv(stack, "admin@example.test")
    job_id = _new_job(stack["db"], uid)
    status, _b = _live(users, job_id)
    assert status == 200
    assert seen.get("readable"), "the live submit got %r, which does not exist" % seen.get("cv")


def _live(users, job_id, on_wait=None, timeout=20):
    """Start a supervised live apply and poll it to the end (it runs in the
    background since 2026-11-08). on_wait(view) is called once when it asks
    for a security code."""
    import time as _t
    st, _l, body = users["admin"].get("/api/admin/apply-test?job_id=%d&mode=live" % job_id)
    v = json.loads(body)
    if st != 200:
        return st, v
    end, asked = _t.time() + timeout, False
    while v.get("state") != "done" and _t.time() < end:
        if v.get("state") == "awaiting_code" and on_wait and not asked:
            asked = True
            on_wait(v)
        _t.sleep(0.1)
        st, _l, body = users["admin"].get("/api/admin/apply-run?id=" + v["run_id"])
        v = json.loads(body)
    return st, v


def test_a_supervised_apply_waits_for_the_emailed_security_code(stack, users, monkeypatch):
    """Greenhouse emails a code after Submit (a real supervised submit, 2026-11-08):
    the run pauses, the page hands the code over, the engine finishes."""
    import apply_engine
    got = {}

    def fake_submit(url, title, company, applicant, cv_path, *a, **k):
        got["code"] = applicant["_await_code"]()
        return {"success": True, "status": "confirmed", "confirmation_text": "Thank you for applying",
                "error": ""}
    monkeypatch.setattr(apply_engine, "submit_application", fake_submit)
    # The fixture job is not on a company site, so the route would first run the
    # real career-page lookup over the network - a 10 s client timeout, 1 run in 9.
    monkeypatch.setattr(apply_engine, "resolve_ats_application", lambda *a, **k: None)
    uid = _user_id(stack["db"], "admin@example.test")
    job_id = _new_job(stack["db"], uid)
    seen = {}

    def hand_over(v):
        seen["left"] = v.get("code_seconds_left")
        st, _l, _b = users["admin"].post_json("/api/admin/apply-run/code", {"run_id": v["run_id"], "code": "bad!"})
        assert st == 400
        st, _l, _b = users["admin"].post_json("/api/admin/apply-run/code", {"run_id": v["run_id"], "code": " AB12 cd34 "})
        assert st == 200
    st, out = _live(users, job_id, on_wait=hand_over)
    assert st == 200 and out["state"] == "done" and out["result"]["status"] == "confirmed"
    assert got["code"] == "AB12cd34" and seen["left"] > 0
    conn = stack["db"].get_db()
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    assert row["status"] == "applied"
    # A finished run takes no code; another user cannot see it.
    st, _l, _b = users["admin"].post_json("/api/admin/apply-run/code", {"run_id": out["run_id"], "code": "AB12CD34"})
    assert st == 409
    st, _l, _b = users["a"].get("/api/admin/apply-run?id=" + out["run_id"])
    assert st == 403


def test_the_selftest_reports_the_live_database_and_the_stored_cv(stack, users):
    _cold_cv(stack, "admin@example.test")
    status, _loc, body = users["admin"].get("/api/admin/apply-selftest")
    diag = json.loads(body)
    assert "db_file" not in diag and "db_exists" not in diag, "still reports the pre-Postgres SQLite file"
    assert diag["db_backend"] in ("sqlite", "postgres")
    assert diag["cv_in_database"] is True
    assert diag["cv_exists"] is True


def test_the_candidate_list_says_which_jobs_the_engine_can_submit_to(stack, users):
    """The Admin picker sorts and labels by this; a job board is manual by design."""
    uid = _user_id(stack["db"], "admin@example.test")
    conn = stack["db"].get_db()
    for url in ("https://boards.greenhouse.io/acme/jobs/123", "https://www.linkedin.com/jobs/view/1"):
        conn.execute("INSERT INTO jobs (user_id,title,company,url,status) VALUES (?,?,?,?,?)",
                     (uid, "PM", "Acme", url, "approved"))
    conn.commit()
    conn.close()
    status, _loc, body = users["admin"].get("/api/admin/apply-test")
    by_url = {j["url"]: j for j in json.loads(body)["jobs"]}
    assert by_url["https://boards.greenhouse.io/acme/jobs/123"]["direct_ats"] is True
    assert by_url["https://www.linkedin.com/jobs/view/1"]["direct_ats"] is False


def test_the_picker_offers_submittable_jobs_he_has_not_approved_yet(stack, users):
    """2026-11-07: his three approved jobs were 2 Workday + 1 board, so the
    supervised test had nothing it could submit."""
    uid = _user_id(stack["db"], "admin@example.test")
    conn = stack["db"].get_db()
    rows = [("https://amat.wd1.myworkdayjobs.com/External/job/X_R1", "approved"),
            ("https://www.linkedin.com/jobs/view/9", "approved"),
            ("https://boards.greenhouse.io/acme/jobs/555", "new"),
            ("https://www.linkedin.com/jobs/view/10", "new")]
    for url, st in rows:
        conn.execute("INSERT INTO jobs (user_id,title,company,url,status) VALUES (?,?,?,?,?)",
                     (uid, "PM", "Acme", url, st))
    conn.commit()
    conn.close()
    _s, _l, body = users["admin"].get("/api/admin/apply-test")
    by_url = {j["url"]: j for j in json.loads(body)["jobs"]}
    assert by_url["https://amat.wd1.myworkdayjobs.com/External/job/X_R1"]["kind"] == "account_needed"
    assert by_url["https://www.linkedin.com/jobs/view/9"]["kind"] == "job_board"
    assert by_url["https://boards.greenhouse.io/acme/jobs/555"]["kind"] == "submittable"
    assert "https://www.linkedin.com/jobs/view/10" not in by_url      # a new board job is not offered


def _tiny_pdf(lines):
    """A real one-page PDF whose text pypdf can read (no extra dependency)."""
    ops = "BT /F1 11 Tf 50 750 Td 14 TL " + " ".join(
        "(%s) Tj T*" % l.replace("(", "[").replace(")", "]") for l in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            "<< /Length %d >>\nstream\n%s\nendstream" % (len(ops), ops)]
    out, offs = "%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += "%d 0 obj\n%s\nendobj\n" % (i, o)
    xref = len(out)
    out += "xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += "".join("%010d 00000 n \n" % o for o in offs)
    out += "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out.encode("latin-1")


def test_the_applicant_is_read_from_the_cv_file_not_the_summary(stack, monkeypatch):
    """Every apply path passed cv_summary (2-3 sentences, no name) to the
    extraction, and pypdf was not even installed in production (2026-11-07)."""
    import app as A
    import apply_engine
    import storage
    uid = _user_id(stack["db"], "admin@example.test")
    pdf = _tiny_pdf(["Alex Tester", "+972-50-000-0000 | Tel Aviv",
                     "Head of Product for Recommendations, 15 years in consumer products.",
                     "Led recommendation and personalization across web, mobile and TV."])
    storage.put(uid, pdf, "cv.pdf")
    import os
    os.remove(storage.cache_path(uid))          # a fresh container: only the database has it
    seen = {}

    def fake_json(prompt, **_k):
        seen["prompt"] = prompt
        return {"full_name": "Alex Tester", "phone": "+972-50-000-0000"}
    monkeypatch.setattr(apply_engine, "GEMINI_KEY", "k")
    monkeypatch.setattr(apply_engine, "_claude_json", fake_json)
    got = A._applicant_for(uid, "admin@example.test", "Admin", storage.cache_path(uid),
                           "A product leader with 15 years of experience.")
    assert "Alex Tester" in seen["prompt"] and "+972-50-000-0000" in seen["prompt"], \
        "the extraction did not get the CV's text"
    assert got["full_name"] == "Admin"          # the account name wins (Profile page)


def test_the_profile_page_wins_over_the_cv(stack, monkeypatch):
    """The owner, 2026-11-07: 'You have everything you need in my profile page'."""
    import app as A
    import apply_engine
    uid = _user_id(stack["db"], "admin@example.test")
    conn = stack["db"].get_db()
    conn.execute("UPDATE user_profiles SET phone=?, linkedin_url=? WHERE user_id=?",
                 ("054-0000000", "https://www.linkedin.com/in/example/", uid))
    conn.commit()
    conn.close()
    monkeypatch.setattr(apply_engine, "GEMINI_KEY", "k")
    monkeypatch.setattr(apply_engine, "_claude_json", lambda *a, **k: {
        "full_name": "Someone Else", "phone": "+1 555 0100", "linkedin_url": "",
        "current_title": "Head of Product"})
    got = A._applicant_for(uid, "me@example.test", "Alex Tester", "", "A product leader " * 10)
    assert (got["full_name"], got["first_name"], got["last_name"]) == ("Alex Tester", "Alex", "Tester")
    assert got["phone"] == "+972 540000000"           # international: pickers default to US +1
    assert got["linkedin_url"] == "https://www.linkedin.com/in/example/"
    assert got["email"] == "me@example.test"
    assert got["current_title"] == "Head of Product"   # the CV still fills what the profile lacks


def test_sponsorship_is_saved_and_reaches_the_applicant(stack, users, monkeypatch):
    """The owner, 2026-11-07: assume no sponsorship for now, but let the profile say
    it for home (Israel) and abroad."""
    import app as A
    import apply_engine
    uid = _user_id(stack["db"], "admin@example.test")
    monkeypatch.setattr(apply_engine, "GEMINI_KEY", "")
    got = A._applicant_for(uid, "a@x.test", "Alex Tester", "", "")
    assert got["needs_sponsorship_in_home_country"] is False      # not answered = no
    assert got["needs_sponsorship_abroad"] is False
    st, _l, _b = users["admin"].post_json("/api/save-profile", {"sponsorship_local": "no", "sponsorship_abroad": "yes"})
    assert st == 200
    got = A._applicant_for(uid, "a@x.test", "Alex Tester", "", "")
    assert got["needs_sponsorship_abroad"] is True and got["needs_sponsorship_in_home_country"] is False
    assert got["home_country"] == "Israel"
    st, _l, _b = users["admin"].post_json("/api/save-profile", {"sponsorship_abroad": "maybe"})
    assert st == 400
    _s, _l, body = users["admin"].get("/api/me")
    assert json.loads(body).get("sponsorship_abroad") == "yes"


def test_answers_are_saved_for_reuse_and_release_the_held_job(stack, users):
    import app as A
    uid = _user_id(stack["db"], "admin@example.test")
    job_id = _new_job(stack["db"], uid)
    conn = stack["db"].get_db()
    conn.execute("UPDATE jobs SET status='approved', apply_status='needs_answers', apply_attempts=2, "
                 "apply_questions=? WHERE id=?",
                 (json.dumps([{"key": "salary expectations", "label": "Salary expectations *",
                               "type": "text", "options": [], "draft": ""}]), job_id))
    conn.commit()
    conn.close()
    st, _l, body = users["admin"].post_json("/api/jobs/%d/answers" % job_id, {"answers": [
        {"key": "salary expectations", "label": "Salary expectations *", "answer": "Open to discuss"}]})
    assert st == 200 and json.loads(body)["saved"] == 1
    conn = stack["db"].get_db()
    row = conn.execute("SELECT apply_status, apply_questions, apply_attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    assert row["apply_status"] is None and row["apply_questions"] is None and row["apply_attempts"] == 0
    assert A._answer_bank(uid)["salary expectations"] == "Open to discuss"
    st, _l, _b = users["admin"].post_json("/api/jobs/%d/answers" % job_id, {"answers": []})
    assert st == 400
    assert A._apply_failure_fields("needs_answers", 1, "needs_answers") == ("needs_answers", None)


def test_a_held_supervised_submit_puts_its_questions_on_the_job(stack, users, monkeypatch, _offline_apply):
    import apply_engine
    held = [{"key": "salary expectations", "label": "Salary expectations *", "type": "text",
             "options": [], "draft": "40k"}]
    monkeypatch.setattr(apply_engine, "submit_application", lambda *a, **k: {
        "success": False, "status": "needs_answers", "questions": held,
        "error": "1 question(s) need your answer", "apply_failure_type": "needs_answers"})
    uid = _user_id(stack["db"], "admin@example.test")
    job_id = _new_job(stack["db"], uid)
    st, out = _live(users, job_id)
    assert st == 200 and out["result"]["questions"] == held
    conn = stack["db"].get_db()
    row = conn.execute("SELECT apply_status, apply_questions FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    assert row["apply_status"] == "needs_answers" and json.loads(row["apply_questions"]) == held


def test_salary_and_notice_period_are_saved_and_reach_the_applicant(stack, users, monkeypatch):
    """The owner, 2026-11-07: a salary expectation section, and notice period 30 days."""
    import app as A
    import apply_engine
    monkeypatch.setattr(apply_engine, "GEMINI_KEY", "")
    uid = _user_id(stack["db"], "admin@example.test")
    _s, _l, body = users["admin"].get("/api/application-answers")
    assert json.loads(body) == {"salary_expectation_min": None, "salary_expectation_currency": "ILS",
                                "notice_period_days": 30, "standing_notes": ""}  # defaults
    got = A._applicant_for(uid, "a@x.test", "Alex Tester", "", "")
    assert got["notice_period"] == "30 days" and "salary_expectation" not in got
    st, _l, body = users["admin"].post_json("/api/application-answers", {
        "salary_expectation_min": "45,000", "salary_expectation_currency": "ils", "notice_period_days": 30})
    assert st == 200 and json.loads(body)["salary_expectation_min"] == 45000
    got = A._applicant_for(uid, "a@x.test", "Alex Tester", "", "")
    assert got["salary_expectation"] == "at least 45,000 ILS gross per month"
    st, _l, _b = users["admin"].post_json("/api/application-answers", {"notice_period_days": 30,
                                                                       "salary_expectation_min": 50000})
    assert st == 200                                                            # the upsert path
    assert A._standing_answers(uid)["salary_expectation_min"] == 50000
    for bad in ({"salary_expectation_currency": "BTC"}, {"notice_period_days": 999},
                {"salary_expectation_min": "lots"}):
        st, _l, _b = users["admin"].post_json("/api/application-answers", bad)
        assert st == 400, bad


def test_standing_instructions_are_kept_on_the_account_and_reach_the_applicant(stack, users, monkeypatch):
    """The owner, 2026-11-08: answers to recurring form questions, kept as one block of
    instructions on the account - not as profile questions, not in the code."""
    import app as A
    import apply_engine
    monkeypatch.setattr(apply_engine, "GEMINI_KEY", "")
    uid = _user_id(stack["db"], "admin@example.test")
    assert "standing_instructions" not in A._applicant_for(uid, "a@x.test", "Alex Tester", "", "")
    notes = "Willing to relocate: yes.\nCurrent salary: do not answer.\nGitHub: https://github.com/example-user"
    st, _l, body = users["admin"].post_json("/api/application-answers", {"standing_notes": "  " + notes + " "})
    assert st == 200 and json.loads(body)["standing_notes"] == notes
    assert A._applicant_for(uid, "a@x.test", "Alex Tester", "", "")["standing_instructions"] == notes
    assert A._applicant_for(uid, "a@x.test", "Alex Tester", "", "")["github_url"] == "https://github.com/example-user"
    # Saving the salary section later leaves the instructions alone.
    users["admin"].post_json("/api/application-answers", {"notice_period_days": 30, "salary_expectation_min": None,
                                                          "salary_expectation_currency": "ILS"})
    assert A._standing_answers(uid)["standing_notes"] == notes
    st, _l, _b = users["admin"].post_json("/api/application-answers", {"standing_notes": "x" * 3001})
    assert st == 400


def test_a_salary_too_low_pass_keeps_the_jobs_salary(stack, users):
    uid = _user_id(stack["db"], "admin@example.test")
    job_id = _new_job(stack["db"], uid)
    conn = stack["db"].get_db()
    conn.execute("UPDATE jobs SET description=? WHERE id=?",
                 ("About the role... Salary: ₪25,000-28,000 per month.", job_id))
    conn.commit()
    conn.close()
    st, _l, _b = users["admin"].post_json("/api/jobs/%d/reject" % job_id, {"reason": "Salary too low"})
    assert st == 200
    conn = stack["db"].get_db()
    row = conn.execute("SELECT salary_monthly, salary_currency FROM rejected_patterns "
                       "WHERE user_id=? ORDER BY rowid DESC LIMIT 1", (uid,)).fetchone()
    conn.close()
    assert (row["salary_monthly"], row["salary_currency"]) == (28000, "ILS")


def test_the_learning_report_is_admin_only_and_answers(stack, users):
    st, _l, body = users["admin"].get("/api/admin/learning-report?scope=all")
    assert st == 200 and "verdict" in json.loads(body)
    st, _l, _b = users["a"].get("/api/admin/learning-report")
    assert st == 403


def test_the_notification_check_is_admin_only_and_reports_each_channel(stack, users, monkeypatch):
    import app as A
    monkeypatch.setattr(A, "send_web_push_to_user", lambda u, m, suf="", url=None, title="Job Hunter": {
        "configured": True, "devices": 0, "sent": 0, "failed": 0, "errors": []})
    # The shared server stubs deliver_notification (offline suite); its real
    # behaviour is tested in test_notifications.py. Here: the route passes it on.
    monkeypatch.setattr(A, "deliver_notification", lambda *a, **k: {"push": "no device has notifications on"})
    st, _l, _b = users["a"].get("/api/admin/notify-check")
    assert st == 403
    st, _l, body = users["admin"].get("/api/admin/notify-check")
    v = json.loads(body)
    assert st == 200 and set(v) >= {"push", "devices", "channels", "recent"}
    st, _l, body = users["admin"].post_json("/api/admin/notify-check", {})
    assert st == 200 and json.loads(body)["results"]["push"] == "no device has notifications on"
    st, _l, body = users["admin"].post_json("/api/push/test", {})
    assert st == 502 and json.loads(body)["success"] is False          # it used to say success


def test_a_background_apply_waits_for_the_users_code_beyond_the_deadline(stack, users, monkeypatch):
    """Option 1 (The owner, 2026-11-08): an unattended apply that hits an emailed
    security code notifies the user and waits; the wait is not deadline time."""
    import threading as _th
    import time as _t
    import app as A
    import apply_engine
    monkeypatch.setattr(A, "_APPLY_DEADLINE_S", 1)
    sent = []
    monkeypatch.setattr(A, "deliver_notification", lambda uid, msg, **k: sent.append((uid, msg, k)) or {"push": "sent to 1 device(s)"})
    uid = _user_id(stack["db"], "alice@example.test")
    job_id = _new_job(stack["db"], uid, title="Director of Product", company="Globex")

    def fake_submit(url, title, company, applicant, *a, **k):
        code = applicant["_await_code"]()
        return {"success": bool(code), "status": "confirmed" if code == "K7Q2ZP9X" else "needs_code",
                "confirmation_text": "", "error": "" if code else "no code"}
    monkeypatch.setattr(apply_engine, "submit_application", fake_submit)
    out = {}
    th = _th.Thread(target=lambda: out.update(r=A._submit_application_guarded(
        "https://boards.greenhouse.io/x/jobs/1", "Director of Product", "Globex", {}, None,
        user_id=uid, job_id=job_id)))
    th.start()
    for _ in range(50):
        st, _l, body = users["a"].get("/api/apply-runs/waiting")
        waiting = json.loads(body)["waiting"]
        if waiting:
            break
        _t.sleep(0.1)
    assert waiting and waiting[0]["company"] == "Globex" and waiting[0]["job_id"] == job_id
    conn = stack["db"].get_db()
    assert conn.execute("SELECT apply_status FROM jobs WHERE id=?", (job_id,)).fetchone()["apply_status"] == "awaiting_code"
    conn.close()
    assert sent and "Globex emailed you a security code" in sent[0][1]
    assert "/app?finish=" + waiting[0]["run_id"] in sent[0][2]["push_url"]
    # Other users see nothing and cannot answer it.
    st, _l, body = users["admin"].get("/api/apply-runs/waiting")
    assert json.loads(body)["waiting"] == []
    st, _l, _b = users["admin"].post_json("/api/apply-runs/code", {"run_id": waiting[0]["run_id"], "code": "K7Q2ZP9X"})
    assert st == 404
    _t.sleep(1.5)                                   # past the 1 s deadline while waiting
    st, _l, _b = users["a"].post_json("/api/apply-runs/code", {"run_id": waiting[0]["run_id"], "code": "K7Q2ZP9X"})
    assert st == 200
    th.join(10)
    assert out["r"]["status"] == "confirmed", out["r"]          # not "Apply exceeded the deadline"


def test_a_missed_code_is_held_for_the_user_not_retried(stack):
    import app as A
    assert A._apply_failure_fields("other", 1, "needs_code") == ("needs_code", None)


def test_the_settings_test_button_uses_the_saved_telegram_details(stack, users, monkeypatch):
    """2026-11-08, the owner: "Failed: telegram". Settings sends only {"channel"}, and
    the route read the token and chat id from the request body - so it called
    Telegram with none. send_telegram used to swallow that and the button said
    "Sent"; since #58 it raised. The saved details must be used."""
    import app as A
    uid = _user_id(stack["db"], "alice@example.test")
    conn = stack["db"].get_db()
    conn.execute("UPDATE user_profiles SET notification_channel='telegram', telegram_token=?, telegram_chat_id=? "
                 "WHERE user_id=?", (A.crypto.encrypt("123:saved") if hasattr(A.crypto, "encrypt") else "123:saved", "42", uid))
    conn.commit()
    conn.close()
    calls = []
    monkeypatch.setattr(A, "send_telegram", lambda t, c, m: calls.append((t, c)))
    st, _l, body = users["a"].post_json("/api/test-notification", {"channel": "telegram"})
    assert st == 200 and json.loads(body).get("success") is True, body
    assert calls == [("123:saved", "42")]
    # Details typed in the form but not saved yet still win.
    users["a"].post_json("/api/test-notification", {"channel": "telegram", "telegram_token": "9:typed",
                                                      "telegram_chat_id": "7"})
    assert calls[-1] == ("9:typed", "7")


def test_ats_names_cover_the_systems_and_embedded_greenhouse():
    import apply_engine as ae
    assert ae.ats_name("https://boards.greenhouse.io/globex/jobs/1") == "Greenhouse"
    assert ae.ats_name("https://www.globex.example/careers/position/1234567/?gh_jid=1234567") == "Greenhouse"
    assert ae.ats_name("https://jobs.lever.co/acme/1") == "Lever"
    assert ae.ats_name("https://www.comeet.co/jobs/x/1") == "Comeet"
    assert ae.ats_name("https://www.linkedin.com/jobs/view/1") == "LinkedIn"
    assert ae.ats_name("https://acme.com/careers/1") == "Company site"


def test_apply_rates_do_not_count_jobs_marked_applied_by_hand_as_engine_sends(stack, users):
    """2026-11-22, proven on production: 56 jobs the engine failed on and the owner
    then marked applied himself (apply_status='submitted', applied_via='manual')
    were counted as the engine's sends - Comeet 78% with no Comeet send ever."""
    uid = _user_id(stack["db"], "admin@example.test")
    conn = stack["db"].get_db()
    rows = [  # url, apply_status, applied_via, failure type
        ("https://www.comeet.com/jobs/acme/1", "submitted", "manual", "captcha"),     # 423, then by hand
        ("https://www.comeet.com/jobs/acme/2", "manual_required", None, "captcha"),   # 423
        ("https://www.comeet.com/jobs/acme/3", "submitted", "manual", "other"),       # engine fault, then by hand
        ("https://www.comeet.com/jobs/acme/4", "submitted", "engine", None),          # a real send
        ("https://www.linkedin.com/jobs/view/5", "submitted", "manual", "unconfirmed"),  # board: by design
    ]
    for i, (url, st, via, ft) in enumerate(rows):
        conn.execute("INSERT INTO jobs (user_id,title,company,url,status,apply_status,applied_via,"
                     "apply_failure_type,apply_attempts) VALUES (?,?,?,?,?,?,?,?,1)",
                     (uid, "PM %d" % i, "ByHand", url + "?byhand", "applied", st, via, ft))
    conn.commit()
    conn.close()
    st, _l, body = users["admin"].get("/api/admin/apply-rates?scope=me")
    by = {r["system"]: r for r in json.loads(body)["systems"]}
    c = by["Comeet"]
    assert (c["sent"], c["blocked"], c["failed"]) == (1, 2, 1), c
    assert c["success_rate"] == 25
    assert by["LinkedIn"]["sent"] == 0 and by["LinkedIn"]["manual"] >= 1
    conn = stack["db"].get_db()
    conn.execute("DELETE FROM jobs WHERE company='ByHand'")
    conn.commit()
    conn.close()


def test_apply_rates_count_engine_attempts_by_system_and_outcome(stack, users):
    """Step 8.3: held and manual are not engine failures."""
    uid = _user_id(stack["db"], "admin@example.test")
    conn = stack["db"].get_db()
    for i, (url, st) in enumerate([
            ("https://boards.greenhouse.io/a/jobs/1", "confirmed"), ("https://boards.greenhouse.io/a/jobs/2", "failed"),
            ("https://boards.greenhouse.io/a/jobs/3", "needs_code"), ("https://jobs.lever.co/a/4", "submitted"),
            ("https://www.linkedin.com/jobs/view/5", "manual_required")]):
        conn.execute("INSERT INTO jobs (user_id,title,company,url,status,apply_status,apply_attempts) VALUES (?,?,?,?,?,?,1)",
                     (uid, "PM %d" % i, "Rates", url + "?rates", "approved", st))
    conn.execute("INSERT INTO jobs (user_id,title,company,url,status,apply_status,apply_attempts) VALUES (?,?,?,?,?,?,0)",
                 (uid, "never tried", "Rates", "https://boards.greenhouse.io/a/jobs/9?rates", "approved", "failed"))
    conn.commit()
    conn.close()
    st, _l, _b = users["a"].get("/api/admin/apply-rates")
    assert st == 403
    st, _l, body = users["admin"].get("/api/admin/apply-rates?scope=me")
    by = {r["system"]: r for r in json.loads(body)["systems"]}
    gh = by["Greenhouse"]
    assert (gh["attempted"], gh["sent"], gh["failed"], gh["held"]) == (3, 1, 1, 1)
    assert gh["success_rate"] == 50 and gh["small_sample"] is True
    assert by["Lever"]["success_rate"] == 100 and by["LinkedIn"]["manual"] == 1


def test_auto_apply_starts_off_for_everyone_but_the_admin(stack):
    """m0022: flags left on from before the paywall must not start applying for
    people who never chose it now (auto-apply opened to every plan, 2026-11-08)."""
    import migrations
    conn = stack["db"].get_db()
    conn.execute("UPDATE user_profiles SET auto_apply_enabled=1")
    conn.commit()
    migrations.m0022_auto_apply_fresh_choice(conn)
    rows = conn.execute("SELECT u.role, p.auto_apply_enabled FROM user_profiles p JOIN users u ON u.id=p.user_id").fetchall()
    conn.close()
    assert rows and all((r["auto_apply_enabled"] == 1) == (r["role"] == "admin") for r in rows), [dict(r) for r in rows]


def test_application_instructions_are_the_owners_alone(stack, users):
    """The owner, 2026-11-16: "my text should be mine only, other users should get a
    general one or an empty one". Instructions are kept per account: another user
    reads an empty box and saving theirs leaves the owner's untouched."""
    import app as A
    mine = "Current salary: do not answer.\nWilling to relocate: no."
    st, _l, _b = users["admin"].post_json("/api/application-answers", {"standing_notes": mine})
    assert st == 200
    _s, _l, body = users["b"].get("/api/application-answers")
    assert json.loads(body)["standing_notes"] == ""
    users["b"].post_json("/api/application-answers", {"standing_notes": "Notice period: 60 days."})
    assert A._standing_answers(_user_id(stack["db"], "admin@example.test"))["standing_notes"] == mine
    _s, _l, body = users["admin"].get("/api/application-answers")
    assert json.loads(body)["standing_notes"] == mine


def test_the_test_account_is_flagged_and_left_out_of_the_funnel(stack, users):
    """The owner, 2026-11-16: Today said 3 new users, the funnel 2 - the third was
    the resettable test account, which is not a person."""
    import app as A
    import testaccount
    st, _l, _b = users["admin"].post_json("/api/admin/test-account/reset", {})
    assert st == 200
    _s, _l, body = users["admin"].get("/api/admin/users")
    flagged = {u["email"]: u["is_test"] for u in json.loads(body)}
    assert flagged[testaccount.TEST_EMAIL] is True and flagged["admin@example.test"] is False
    tid = _user_id(stack["db"], testaccount.TEST_EMAIL)
    before = A.funnel_report(30)["steps"][0]["users"]
    assert A._funnel(tid, "signup")                     # even if it ever gets a signup event...
    assert A.funnel_report(30)["steps"][0]["users"] == before   # ...the cohort leaves it out


def test_running_cost_is_admin_only(stack, users):
    st, _l, body = users["admin"].get("/api/admin/running-cost?days=30")
    assert st == 200 and "per_active_user_usd" in json.loads(body)
    st, _l, _b = users["b"].get("/api/admin/running-cost")
    assert st == 403


def test_signup_keeps_where_it_came_from_and_the_kpis_are_admin_only(stack, users):
    """GTM row 10a part 2: Growth breaks sign-ups down by source, so the source
    is kept on the account at sign-up."""
    from tests.test_routes import Client
    import ratelimit
    ratelimit.reset_all()
    c = Client(stack["port"])
    st, _loc, _b = c.post_form("/register", {
        "name": "src", "email": "src@example.test", "password": "correct-horse-1", "password2": "correct-horse-1",
        "utm_source": "facebook", "utm_medium": "group", "noa": "b"})
    assert st == 302
    conn = stack["db"].get_db()
    raw = conn.execute("SELECT signup_attr FROM users WHERE email='src@example.test'").fetchone()[0]
    conn.close()
    assert json.loads(raw) == {"noa": "b", "utm_medium": "group", "utm_source": "facebook"}
    for path in ("/api/admin/kpis", "/api/admin/sources?days=30"):
        st, _l, body = users["admin"].get(path)
        assert st == 200, path
        st, _l, _b = users["b"].get(path)
        assert st == 403, path
    _s, _l, body = users["admin"].get("/api/admin/sources?days=30")
    assert any(r["name"] == "facebook / group" for r in json.loads(body)["sources"])


def test_meta_events_follow_the_cookie_and_only_the_cookie(stack, users, monkeypatch):
    """GTM row 16: sign-up and first approve are reported to Meta only for someone
    whose browser carries Meta's _fbp cookie (they accepted the banner)."""
    import app as A
    import metapixel
    from tests.test_routes import Client
    import ratelimit
    calls = []
    monkeypatch.setattr(metapixel, "PIXEL_ID", "1"); monkeypatch.setattr(metapixel, "TOKEN", "t")
    monkeypatch.setattr(metapixel, "send", lambda ev, ids, ua, ip, url, email="": calls.append((ev, dict(ids), url, email)) or True)
    fbp = "fb.1.1763280000000.1234567890"
    for email, cookie in (("yes@example.test", "_fbp=%s" % fbp), ("no@example.test", "")):
        ratelimit.reset_all()
        c = Client(stack["port"])
        c.cookie = cookie
        st, _loc, _b = c.post_form("/register", {"name": "m", "email": email, "password": "correct-horse-1",
                                                 "password2": "correct-horse-1"})
        assert st == 302
    conn = stack["db"].get_db()
    kept = dict(conn.execute("SELECT email, meta_ids FROM users WHERE email LIKE '%@example.test'").fetchall())
    conn.close()
    assert kept["no@example.test"] is None
    assert json.loads(kept["yes@example.test"]) == {"fbp": fbp}
    assert [(e, i, m) for e, i, _u, m in calls] == [("CompleteRegistration", {"fbp": fbp}, "yes@example.test")]
    assert calls[0][2].endswith("/register")
    # First approve: from the ids kept at sign-up (another device, no cookie), once, then forgotten.
    uid = _user_id(stack["db"], "yes@example.test")

    class _H:
        headers = {"User-Agent": "UA"}
        def client_ip(self):
            return "203.0.113.9"
    A._meta_event(_H(), uid, "FirstApprove")
    assert calls[-1][0] == "FirstApprove" and calls[-1][1] == {"fbp": fbp}
    conn = stack["db"].get_db()
    left = conn.execute("SELECT meta_ids FROM users WHERE id=?", (uid,)).fetchone()[0]
    conn.close()
    assert left is None
    n = len(calls)
    A._meta_event(_H(), _user_id(stack["db"], "no@example.test"), "FirstApprove")
    assert len(calls) == n                                  # never accepted: nothing sent


def test_a_paused_user_is_skipped_by_the_scheduler_and_resumes_on_opening_the_app(stack, users, monkeypatch):
    """GTM row 26: paused for inactivity = no scheduled search; opening the app
    (or the email link, or the admin) resumes and queues a search at once."""
    import app as A
    import inactive
    import jobqueue
    uid = _user_id(stack["db"], "bob@example.test")
    queued = []
    monkeypatch.setattr(jobqueue, "enqueue", lambda u, kind, **kw: queued.append((u, kind)) or 1)

    def pause():
        conn = stack["db"].get_db()
        conn.execute("UPDATE users SET search_paused_at='2026-12-20 09:00:00' WHERE id=?", (uid,))
        conn.commit(); conn.close()

    def paused():
        conn = stack["db"].get_db()
        v = conn.execute("SELECT search_paused_at FROM users WHERE id=?", (uid,)).fetchone()[0]
        conn.close()
        return v

    pause()
    _s, _l, body = users["admin"].get("/api/admin/users")
    assert [u for u in json.loads(body) if u["id"] == uid][0]["search_paused_at"]
    st, _l, _b = users["b"].get("/api/me")                               # opening the app
    assert st == 200 and paused() is None and queued == [(uid, "search")]
    users["b"].get("/api/me")
    assert queued == [(uid, "search")]                                   # not paused: nothing more is queued
    pause()
    st, _l, body = Client(stack["port"]).get("/email/resume?u=%d&t=%s" % (uid, inactive.resume_token(uid)))
    assert st == 200 and b"searching again" in body and paused() is None
    st, _l, _b = Client(stack["port"]).get("/email/resume?u=%d&t=bad" % uid)
    assert st == 400
    pause()
    st, _l, body = users["admin"].post_json("/api/admin/users/%d/resume-search" % uid, {})
    assert st == 200 and json.loads(body)["resumed"] is True and paused() is None
    st, _l, _b = users["b"].post_json("/api/admin/users/%d/resume-search" % uid, {})
    assert st == 403
    src = open(A.__file__, encoding="utf-8").read()
    assert "_search_runs_today(freq, s_dow, wo, row[12], cur_dow)" in src and "u.search_paused_at " in src


def test_the_run_timeline_is_admin_only_and_carries_each_runs_timestamps(stack, users):
    """/api/admin/runs (2026-11-22): queued / started / finished per run, so a late
    search can be told apart as a queue wait or a slow run."""
    import jobqueue
    uid = _user_id(stack["db"], "alice@example.test")
    rid = jobqueue.enqueue(uid, "search", cap=False)
    st, _l, _b = users["a"].get("/api/admin/runs")
    assert st == 403
    st, _l, body = users["admin"].get("/api/admin/runs")
    v = json.loads(body)
    assert st == 200 and v["times"] == "UTC"
    row = [r for r in v["runs"] if r["id"] == rid][0]
    assert row["user_id"] == uid and row["kind"] == "search" and row["created_date"]
    assert {"started_date", "finished_date", "status"} <= set(row)
    st, _l, body = users["admin"].get("/api/admin/runs?day=2001-01-01")
    assert json.loads(body)["runs"] == []
    st, _l, body = users["admin"].get("/api/admin/runs?day=bogus")   # falls back to today
    assert any(r["id"] == rid for r in json.loads(body)["runs"])
    db = stack["db"].get_db()
    try:
        db.execute("DELETE FROM job_runs WHERE id=?", (rid,))
        db.commit()
    finally:
        db.close()
