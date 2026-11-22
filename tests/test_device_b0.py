"""
tests/test_device_b0.py - Plan B, test B0 (device.py): the owner's own device
sends the application; the server pairs it, hands it the prepared answers and
the CV, and records what the site answered.

Owner-only (the owner, 2026-11-22): codes come from an admin session, tokens work
only for an admin account, and a stranger without a token gets nothing.
"""
import json

from tests.test_routes import (Client, stack, users, _user_id, _new_job)  # noqa: F401


def _pair(stack, users):
    st, _l, body = users["admin"].get("/api/admin/device-code")
    assert st == 200, body
    code = json.loads(body)["code"]
    anon = Client(stack["port"])
    st, _l, body = anon.post_json("/api/device/pair", {"code": code, "label": "Chrome on the Mac"},
                                  extra_headers={"Sec-Fetch-Site": "none"})
    assert st == 200, body
    return anon, {"Authorization": "Bearer " + json.loads(body)["token"]}


def test_only_an_admin_gets_a_code_and_a_code_works_once(stack, users):
    st, _l, _b = users["a"].get("/api/admin/device-code")
    assert st == 403
    anon, _auth = _pair(stack, users)
    # The same code again: used up.
    st, _l, body = users["admin"].get("/api/admin/device-code")
    code = json.loads(body)["code"]
    assert anon.post_json("/api/device/pair", {"code": code})[0] == 200
    assert anon.post_json("/api/device/pair", {"code": code})[0] == 400
    assert anon.post_json("/api/device/pair", {"code": "000000"})[0] == 400


def test_without_a_token_the_device_api_answers_nothing(stack, users):
    anon = Client(stack["port"])
    assert anon.get("/api/device/jobs")[0] == 401
    assert anon.get("/api/device/jobs", {"Authorization": "Bearer not-a-token"})[0] == 401
    assert anon.post_json("/api/device/attempt", {"job_id": 1, "outcome": "sent"})[0] == 401


def test_the_device_gets_the_prepared_answers_and_a_sent_attempt_moves_the_job(stack, users):
    db = stack["db"]
    uid = _user_id(db, "admin@example.test")
    jid = _new_job(db, uid, title="Senior PM", company="Comeet Co")
    conn = db.get_db()
    conn.execute("UPDATE jobs SET status='approved', url=?, apply_status='manual_required', "
                 "apply_failure_type='prepared', apply_prepared=? WHERE id=?",
                 ("https://www.comeet.com/jobs/acme/1/b0-%d" % jid,
                  json.dumps([{"label": "First name", "value": "Eran"}]), jid))
    conn.commit()
    conn.close()
    anon, auth = _pair(stack, users)

    st, _l, body = anon.get("/api/device/jobs", auth)
    assert st == 200
    job = next(j for j in json.loads(body)["jobs"] if j["id"] == jid)
    assert job["system"] == "Comeet" and job["route"] == "prepare"
    assert job["prepared"] == [{"label": "First name", "value": "Eran"}]

    # A bot check: logged, the job stays where it is.
    st, _l, _b = anon.post_json("/api/device/attempt", {"job_id": jid, "outcome": "blocked", "site_status": "423",
                                                       "pressed_by": "extension", "device": "chrome"}, auth)
    assert st == 200
    # Sent: the job is applied, sent by the device, and the rates count it as sent.
    st, _l, _b = anon.post_json("/api/device/attempt", {"job_id": jid, "outcome": "sent", "site_status": "200",
                                                       "pressed_by": "extension", "device": "chrome"}, auth)
    assert st == 200
    conn = db.get_db()
    row = conn.execute("SELECT status, applied_via, apply_status FROM jobs WHERE id=?", (jid,)).fetchone()
    conn.close()
    assert (row["status"], row["applied_via"], row["apply_status"]) == ("applied", "device", "submitted")

    st, _l, body = users["admin"].get("/api/admin/device-attempts")
    got = [a for a in json.loads(body)["attempts"] if a["job_id"] == jid]
    assert [a["outcome"] for a in got] == ["sent", "blocked"]
    assert got[1]["site_status"] == "423" and got[1]["system"] == "Comeet"


def test_a_token_stops_working_when_the_account_is_no_longer_admin(stack, users):
    db = stack["db"]
    anon, auth = _pair(stack, users)
    assert anon.get("/api/device/jobs", auth)[0] == 200
    conn = db.get_db()
    conn.execute("UPDATE users SET role='user' WHERE email='admin@example.test'")
    conn.commit()
    try:
        assert anon.get("/api/device/jobs", auth)[0] == 401
    finally:
        conn.execute("UPDATE users SET role='admin' WHERE email='admin@example.test'")
        conn.commit()
        conn.close()


def test_the_cv_goes_out_named_after_the_applicant_never_cv_pdf(tmp_path):
    """The owner, 2026-11-22: the CV arrived at DriveNets as "cv.pdf"; it is
    "Eran Ganot CV 2026" - for the server engine and the device alike."""
    import datetime
    import apply_engine as ae
    year = datetime.date.today().year
    assert ae.cv_display_name("Eran Ganot") == "Eran Ganot CV %d.pdf" % year
    assert ae.cv_display_name("", ".docx") == "CV %d.docx" % year
    src = tmp_path / "cv.pdf"
    src.write_bytes(b"%PDF-1.4 x")
    out = ae._named_cv(str(src), {"full_name": "Eran Ganot"})
    assert out.endswith("/Eran Ganot CV %d.pdf" % year) and open(out, "rb").read() == b"%PDF-1.4 x"
    assert src.exists()
    out2 = ae._named_cv(str(src), {"first_name": "Eran", "last_name": "Ganot"})
    assert out2.endswith("Eran Ganot CV %d.pdf" % year)


def test_a_job_added_by_link_lands_approved_named_from_its_page(stack, users, monkeypatch):
    """B0 (the owner, 2026-11-22): his search had no Lever or SmartRecruiters
    jobs, so the extension adds one by its link - approved, owner-only, only
    the three systems under test."""
    import device
    monkeypatch.setattr(device, "_page_meta", lambda url: {
        "title": "Acme Robotics - Senior Product Manager" if "lever" in url else "",
        "company": "", "description": "Build the robot."})
    anon, auth = _pair(stack, users)

    st, _l, body = anon.post_json("/api/device/jobs/add", {"url": "https://jobs.lever.co/acme-robotics/abc-123"}, auth)
    got = json.loads(body)
    assert st == 200 and got["ok"] and not got["existing"], got
    assert (got["company"], got["title"], got["system"]) == ("Acme Robotics", "Senior Product Manager", "Lever")

    # The page told nothing (DataDome): the link's own path names the company.
    st, _l, body = anon.post_json("/api/device/jobs/add",
                                  {"url": "https://jobs.smartrecruiters.com/WixCom/744000160000001"}, auth)
    assert json.loads(body)["company"] == "Wixcom"

    # The same link again: the job already there, not a second one.
    st, _l, body = anon.post_json("/api/device/jobs/add", {"url": "https://jobs.lever.co/acme-robotics/abc-123"}, auth)
    assert json.loads(body)["existing"] is True

    st, _l, body = anon.get("/api/device/jobs", auth)
    mine = [j for j in json.loads(body)["jobs"] if j["url"] == "https://jobs.lever.co/acme-robotics/abc-123"]
    assert len(mine) == 1 and mine[0]["system"] == "Lever"

    # Anything else is refused; so is a caller without a token.
    st, _l, body = anon.post_json("/api/device/jobs/add", {"url": "https://www.linkedin.com/jobs/view/1"}, auth)
    assert st == 400 and "LinkedIn" in json.loads(body)["error"]
    assert anon.post_json("/api/device/jobs/add", {"url": "https://jobs.lever.co/x/1"})[0] == 401
