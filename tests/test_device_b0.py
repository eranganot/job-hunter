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


def test_the_device_gets_the_cover_letter_as_text_and_as_a_named_pdf(stack, users, monkeypatch):
    """The owner, 2026-11-22: the letter is written if the job has none (the
    existing writer), saved on the job, and served as a PDF named after him."""
    import ai_analysis
    calls = []

    def writer(job, profile, key="", user_id=None, cv_text="", name="", llm=None):
        calls.append(job["id"])
        return {"letter": "Dear Team,\n\nI want this role because it is mine.\n\nBest,\nAdmin", "notes": []}
    monkeypatch.setattr(ai_analysis, "write_cover_letter", writer)
    monkeypatch.setattr(ai_analysis, "write_note", lambda job, profile, key="", **kw: "I build personalization at scale and this team's problem is one I know.")
    db = stack["db"]
    uid = _user_id(db, "admin@example.test")
    jid = _new_job(db, uid, title="Senior PM", company="Letter Co")
    conn = db.get_db()
    if not conn.execute("SELECT 1 FROM user_profiles WHERE user_id=?", (uid,)).fetchone():
        conn.execute("INSERT INTO user_profiles (user_id) VALUES (?)", (uid,))
    conn.execute("UPDATE user_profiles SET cv_summary='Ten years of product.' WHERE user_id=?", (uid,))
    conn.execute("UPDATE jobs SET status='approved' WHERE id=?", (jid,))
    conn.commit()
    conn.close()
    anon, auth = _pair(stack, users)

    st, _l, body = anon.get("/api/device/jobs/%d/cover-letter" % jid, auth)
    assert st == 200, body
    got = json.loads(body)
    assert got["short"] == "I build personalization at scale and this team's problem is one I know."   # the note, not the letter's opening
    assert got["letter"].startswith("Dear Team")
    anon.get("/api/device/jobs/%d/cover-letter" % jid, auth)
    assert calls == [jid]                                   # written once, then reused from the job
    conn = db.get_db()
    assert "mine" in conn.execute("SELECT cover_letter FROM jobs WHERE id=?", (jid,)).fetchone()[0]
    conn.close()

    # Someone else's job: nothing.
    other = _new_job(db, _user_id(db, "alice@example.test"), title="X", company="Y")
    assert anon.get("/api/device/jobs/%d/cover-letter" % other, auth)[0] == 404
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", stack["port"], timeout=60)
    c.request("GET", "/api/device/jobs/%d/cover-letter?format=pdf" % jid, headers=auth)
    r = c.getresponse()
    pdf = r.read()
    if r.status == 503 and b"Executable doesn" in pdf:
        import pytest
        pytest.skip("no Playwright Chromium here (production has it)")
    assert r.status == 200 and pdf[:4] == b"%PDF", (r.status, pdf[:200])
    assert "Cover%20Letter" in r.getheader("Content-Disposition")


def test_short_note_is_the_letters_first_body_paragraph():
    import coverpdf
    letter = "Dear Hiring Manager,\n\nFirst paragraph here. Second sentence.\n\nMore.\n\nBest regards,\nEran"
    assert coverpdf.short_note(letter) == "First paragraph here. Second sentence."
    assert coverpdf.display_name("Eran Ganot").startswith("Eran Ganot Cover Letter ")
    assert coverpdf.short_note("x" * 50 + ". " + "y" * 900, limit=100).endswith(".")


def test_a_machine_sounding_draft_gets_one_rewrite_with_its_phrases_named():
    """The owner, 2026-11-23, on the Commit letter: "this is not written by human".
    That draft opened "The AI R&D Forward Deployment Engineer role at Commit ...
    aligns well with my experience" - both are caught and named in the rewrite."""
    import ai_analysis
    job = {"id": 1, "title": "AI R&D Forward Deployment Engineer", "company": "Commit",
           "description": "Guide AI adoption."}
    bad = ("Dear Hiring Team,\n\nThe AI R&D Forward Deployment Engineer role at Commit aligns well with my "
           "experience. This direct experience is directly relevant.\n\nBest regards,\nEran")
    good = "Dear Hiring Team,\n\nFor the last few years I've been getting non-technical teams to use ML.\n\nBest regards,\nEran"
    prompts = []

    def llm(p):
        prompts.append(p)
        return bad if len(prompts) == 1 else good
    out = ai_analysis.write_cover_letter(job, {"cv_summary": "Product leader."}, "k", cv_text="CV text", name="Eran", llm=llm)
    assert out["letter"] == good and len(prompts) == 2
    assert "aligns" in prompts[1] and "directly relevant" in prompts[1] and "restating the job title" in prompts[1]
    assert ai_analysis.robot_phrases(good) == []


def test_the_note_is_written_by_its_own_prompt_and_capped():
    import ai_analysis
    job = {"title": "Head of Product", "company": "Fullpath", "description": "CRM for dealers."}
    seen = []
    note = ai_analysis.write_note(job, {"cv_summary": "Product leader."}, "k", cv_text="CV", name="Eran",
                                  llm=lambda p: seen.append(p) or ("I've spent ten years on personalization. " * 20))
    assert len(seen) == 1 and "personal note" in seen[0] and len(note) <= 450


def test_the_job_list_carries_the_applicants_contact_details(stack, users):
    """For the fields every form has, where nothing was prepared (jobs added by link)."""
    db = stack["db"]
    uid = _user_id(db, "admin@example.test")
    conn = db.get_db()
    if not conn.execute("SELECT 1 FROM user_profiles WHERE user_id=?", (uid,)).fetchone():
        conn.execute("INSERT INTO user_profiles (user_id) VALUES (?)", (uid,))
    conn.execute("UPDATE user_profiles SET phone='054-123-4567', linkedin_url='https://www.linkedin.com/in/x', "
                 "locations='[\"Tel Aviv, Israel\", \"San Diego\"]' WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    anon, auth = _pair(stack, users)
    st, _h, body = anon.get("/api/device/jobs", auth)
    a = json.loads(body)["applicant"]
    assert a["email"] == "admin@example.test" and a["phone"].startswith("+972") and a["phone_local"].startswith("05"), a
    assert a["linkedin_url"] == "https://www.linkedin.com/in/x" and a["first_name"], a
    assert a["city"] == "Tel Aviv", a        # from user_profiles.locations (JSON), not '["Tel Aviv' or empty


def test_the_note_never_restates_the_cv_summary_or_labels_the_applicant():
    """The owner, 2026-11-23, on Fullpath's note ("I'm an Executive Product Leader,
    transforming platforms with AI..."): "a copy of my resume summary"."""
    import ai_analysis
    summ = ("An Executive Product Leader with over a decade of experience in AI-powered product "
            "transformation across sports-tech, gaming, and enterprise SaaS.")
    bad = ("I'm an Executive Product Leader, transforming platforms with AI. I'm drawn to how you're using AI "
           "to disrupt the automotive industry. At DAZN, I boosted engagement +22%.")
    good = ("Getting a dealer's sales team to actually use the data in front of them is the part I'd want to dig into. "
            "At DAZN we rebuilt the experience around personalization and retention went up 36%.")
    seen = []
    note = ai_analysis.write_note({"title": "Head of Product", "company": "Fullpath", "description": "CRM for dealers"},
                                  {"cv_summary": summ}, "k", cv_text="DAZN retention 36% engagement 22%", name="Eran",
                                  llm=lambda p: seen.append(p) or (bad if len(seen) == 1 else good))
    assert note == good and len(seen) == 2
    assert summ not in seen[0]                                  # the summary is not in the note's prompt
    for x in ("I'm an Executive Product Leader", "I'm drawn to", "boosted", "+22%"):
        assert x in seen[1], x
    assert ai_analysis._copied_from(bad, summ) == "an executive product leader"


def test_the_extension_trace_is_kept_for_the_admin_to_read(stack, users):
    anon, auth = _pair(stack, users)
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", stack["port"], timeout=30)
    c.request("POST", "/api/device/trace", body=json.dumps({"job_id": 5, "url": "https://x/form", "top": True, "version": "0.6.1",
              "events": [{"step": "pick-miss", "data": {"want": "tel aviv", "seen": [0, 0]}}]}),
              headers=dict(auth, **{"Content-Type": "application/json"}))
    assert c.getresponse().status == 200
    st, _h, body = users["admin"].get("/api/admin/device-trace")
    ev = json.loads(body)["events"]
    assert st == 200 and ev[0]["step"] == "pick-miss" and ev[0]["data"]["want"] == "tel aviv" and ev[0]["version"] == "0.6.1"
    assert users["a"].get("/api/admin/device-trace")[0] == 403


def test_the_answerer_gets_years_of_experience_and_today(stack, users, monkeypatch):
    """The owner, 2026-11-23: Wix got "4-6 years" for his 10; ServiceNow's "Today's
    date" got 2024-05-24 from the model."""
    import app as _app
    import apply_engine as _ae
    import datetime as _dt
    import questions
    monkeypatch.setattr(_ae, "extract_applicant_data", lambda text, email, fallback_name="": {"email": email})
    db = stack["db"]
    uid = _user_id(db, "admin@example.test")
    conn = db.get_db()
    if not conn.execute("SELECT 1 FROM user_profiles WHERE user_id=?", (uid,)).fetchone():
        conn.execute("INSERT INTO user_profiles (user_id) VALUES (?)", (uid,))
    conn.execute("UPDATE user_profiles SET experience_years=10 WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    a = _app._applicant_for(uid, "admin@example.test", "Admin Person", "", "")
    assert a["years_of_experience"] == 10 and a["today"] == _dt.date.today().isoformat(), a
    assert "years_of_experience" in questions._PROMPT and "today from the applicant data" in questions._PROMPT
