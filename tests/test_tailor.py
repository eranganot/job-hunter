"""Step 14: the tailored CV and its fact check."""
import io
import json

import tailor
from tests.test_routes import stack, users, _user_id, _new_job  # noqa: F401

CV = """Eran Ganot
Head of Product, Personalization - DAZN (2021 - Present)
- Led a team of 12 PMs across 3 squads; grew engagement 18%.
- Ran 1,200 A/B tests a year on the recommendation engine.
Senior Product Manager - Wix (2016 - 2021)
- Launched Wix ADI to 2M users.
Skills: SQL, Python, experimentation, roadmapping
B.Sc. Industrial Engineering, Tel Aviv University (2012)
"""

JOB = {"title": "Director of Product", "company": "Payoneer",
       "description": "Lead product for onboarding. Experimentation, data, cross-functional leadership. " * 3}


def _model(prompt):
    assert prompt == "" or "may NOT add anything" in prompt
    return {
        "headline": "Head of Product, Personalization",
        "summary": "Product leader in personalization. Led 12 PMs. Grew revenue 40%.",
        "skills": ["experimentation", "SQL", "Kubernetes"],
        "experience": [
            {"title": "Head of Product, Personalization", "company": "DAZN", "dates": "2021 - Present",
             "bullets": ["Ran 1200 A/B tests a year", "Grew engagement 18% across 3 squads", "Cut churn 25%"]},
            {"title": "VP Product", "company": "Wix", "dates": "2016 - 2021", "bullets": ["Launched Wix ADI to 2M users"]},
            {"title": "CPO", "company": "Google", "dates": "2010 - 2012", "bullets": ["Ran Search"]},
        ],
        "education": ["B.Sc. Industrial Engineering, Tel Aviv University (2012)", "MBA, Harvard (2015)"],
        "other": [],
        "changes": ["Put experimentation first: the job asks for it."],
    }


def test_the_fact_check_removes_what_the_cv_does_not_say():
    res = tailor.tailor(CV, JOB, llm=_model)
    cv = res["cv"]
    why = {r["text"]: r["why"] for r in res["removed"]}
    assert "Grew revenue 40%." in why and "40" in why["Grew revenue 40%."]           # invented number
    assert cv["summary"] == "Product leader in personalization. Led 12 PMs."
    assert "Kubernetes" in why and cv["skills"] == ["experimentation", "SQL"]         # invented skill
    assert [r["company"] for r in cv["experience"]] == ["DAZN", "Wix"]                 # invented employer
    assert any("Google" in t for t in why)
    dazn = cv["experience"][0]["bullets"]
    assert "Ran 1200 A/B tests a year" in dazn                                          # "1,200" in the CV
    assert "Cut churn 25%" not in dazn and "Cut churn 25%" in why
    assert "MBA, Harvard (2015)" in why
    assert any('"VP Product"' in f for f in res["flags"])                               # inflated title: flagged


def test_no_job_description_or_no_cv_is_a_readable_refusal():
    import pytest
    with pytest.raises(RuntimeError, match="no description"):
        tailor.tailor(CV, {"title": "PM", "description": "short"}, llm=_model)
    with pytest.raises(RuntimeError, match="upload your CV"):
        tailor.tailor("", JOB, llm=_model)


def test_the_word_file_has_the_account_header_and_the_checked_body():
    from docx import Document
    res = tailor.tailor(CV, JOB, llm=_model)
    data = tailor.to_docx(res["cv"], name="Eran Ganot", contact=["e@x.test", "+972 54 000 0000", ""])
    text = "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
    assert text.startswith("Eran Ganot\ne@x.test | +972 54 000 0000")
    assert "Ran 1200 A/B tests a year" in text and "Cut churn" not in text and "Google" not in text


def test_the_route_stores_meters_and_serves_the_word_file(stack, users, monkeypatch):
    import app as A
    import plans
    monkeypatch.setattr(tailor, "tailor", lambda cv_text, job, **k: tailor.verify(_model(""), CV))
    monkeypatch.setattr(A, "_tailor_inputs", lambda uid, user: (CV, "Alice", ["alice@example.test"]))
    monkeypatch.setitem(plans.PLANS["free"], "tailored_cv", 2)
    uid = _user_id(stack["db"], "alice@example.test")
    conn = stack["db"].get_db()                       # new accounts start on a Premium promotion
    conn.execute("UPDATE users SET plan='free', plan_until=NULL WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    job_id = _new_job(stack["db"], uid, title="Director of Product", company="Payoneer")
    st, _l, _b = users["a"].get("/api/jobs/%d/tailored-cv" % job_id)
    assert st == 404
    for _ in range(2):
        st, _l, body = users["a"].post_json("/api/jobs/%d/tailored-cv" % job_id, {})
        assert st == 200, body
        assert json.loads(body)["cv"]["experience"][0]["company"] == "DAZN"
    st, _l, body = users["a"].post_json("/api/jobs/%d/tailored-cv" % job_id, {})
    assert st == 402 and json.loads(body)["code"] == "upgrade_required"               # Free: 2 a month
    st, _l, body = users["a"].get("/api/jobs/%d/tailored-cv" % job_id)
    assert st == 200 and json.loads(body)["removed"]
    st, _l, body = users["a"].get("/api/jobs/%d/tailored-cv.docx" % job_id)
    assert st == 200 and body[:2] == b"PK"                                              # a real .docx (zip)
    st, _l, _b = users["b"].get("/api/jobs/%d/tailored-cv" % job_id)
    assert st == 404                                                                    # not another user's


def test_the_user_reviews_edits_and_chooses_which_cv_is_sent(stack, users, monkeypatch):
    """Eran, 2026-11-08: review and edit every draft; then choose tailored or original."""
    import app as A
    monkeypatch.setattr(tailor, "tailor", lambda cv_text, job, **k: tailor.verify(_model(""), CV))
    monkeypatch.setattr(A, "_tailor_inputs", lambda uid, user: (CV, "Bob", ["bob@example.test"]))
    uid = _user_id(stack["db"], "bob@example.test")
    job_id = _new_job(stack["db"], uid, title="Director of Product", company="Payoneer")
    path = "/api/jobs/%d/tailored-cv" % job_id
    st, _l, _b = users["b"].post_json(path, {"action": "use", "use": "tailored"})
    assert st == 404                                                         # nothing to choose yet
    st, _l, body = users["b"].post_json(path, {})
    assert st == 200 and json.loads(body)["use"] == "original"               # original until chosen
    assert A._cv_for_apply(uid, job_id, "/orig.pdf") == "/orig.pdf"
    edited = json.loads(body)["cv"]
    edited["summary"] = "Grew revenue 40% at DAZN."                          # the user's own claim
    edited["experience"][0]["bullets"] = ["Led 12 PMs", ""]
    st, _l, body = users["b"].post_json(path, {"action": "save", "cv": edited})
    got = json.loads(body)
    assert st == 200 and got["edited"] is True
    assert got["cv"]["summary"] == "Grew revenue 40% at DAZN."               # not fact-checked: theirs
    assert got["cv"]["experience"][0]["bullets"] == ["Led 12 PMs"]
    st, _l, _b = users["b"].post_json(path, {"action": "save", "cv": {**edited, "experience": []}})
    assert st == 400
    st, _l, _b = users["b"].post_json(path, {"action": "use", "use": "both"})
    assert st == 400
    st, _l, body = users["b"].post_json(path, {"action": "use", "use": "tailored"})
    assert st == 200 and json.loads(body)["use"] == "tailored"
    sent = A._cv_for_apply(uid, job_id, "/orig.pdf")
    assert sent.endswith("_CV.docx")
    from docx import Document
    text = "\n".join(p.text for p in Document(sent).paragraphs)
    assert "Grew revenue 40% at DAZN." in text and text.startswith("Bob")
    assert A._cv_for_apply(uid, None, "/orig.pdf") == "/orig.pdf"
    st, _l, body = users["b"].post_json(path, {})                           # a new draft: review again
    assert json.loads(body)["use"] == "original" and json.loads(body)["edited"] is False
    assert A._cv_for_apply(uid, job_id, "/orig.pdf") == "/orig.pdf"


# The layout of Eran's real CV (employer line, then "Title 2025-Present"),
# with made-up content.
CV2 = """DANA LEVI
Product Leader
EXPERIENCE
NOVA TLV, Israel
Head of Product 2025-Present
Own personalization strategy and infrastructure, bridging ML capabilities into
user-facing journeys
● Led a move to a personalized experience - boosting engagement +22% and retention
+36%
● Cut delivery cycles by 30%.
ORBIT GAMES TLV, Israel
Product Group Lead 2023-2025
● Built a product group of 3 PMs and 4 engineering teams
EDUCATION
SOME UNIVERSITY NYC, USA
MBA 2019-2021
"""


def test_roles_are_read_from_the_cv_itself():
    roles = tailor.cv_roles(CV2)
    assert [(r["key"], r["title"], r["dates"]) for r in roles] == [
        ("nova", "Head of Product", "2025-Present"), ("orbit", "Product Group Lead", "2023-2025")]
    assert roles[0]["bullets"][1] == "Led a move to a personalized experience - boosting engagement +22% and retention +36%"
    assert len(roles[0]["bullets"]) == 3 and roles[1]["bullets"] == ["Built a product group of 3 PMs and 4 engineering teams"]
    inline = tailor.cv_roles("EXPERIENCE\nSenior PM - Wix (2016 - 2021)\n- Launched things.\n")
    assert (inline[0]["title"], inline[0]["company"], inline[0]["dates"]) == ("Senior PM", "Wix", "2016 - 2021")


def _job2():
    return {"title": "Director of Product", "company": "Payoneer", "description": "Lead product for onboarding. " * 6}


def test_a_current_role_the_model_drops_gets_a_retry_naming_it():
    seen = []
    without = {"headline": "", "summary": "", "skills": [], "education": [], "other": [], "changes": [],
               "experience": [{"title": "Product Group Lead", "company": "Orbit Games", "dates": "2023-2025",
                               "bullets": ["Built a product group of 3 PMs and 4 engineering teams"]}]}
    with_it = {**without, "experience": [{"title": "Head of Product", "company": "Nova", "dates": "2025-Present",
                                          "bullets": ["Cut delivery cycles by 30%."]}] + without["experience"]}
    res = tailor.tailor(CV2, _job2(), llm=lambda p: seen.append(p) or (without if len(seen) == 1 else with_it))
    assert len(seen) == 2 and "Head of Product at NOVA TLV, Israel (2025-Present)" in seen[1]
    assert [r["company"] for r in res["cv"]["experience"]] == ["Nova", "Orbit Games"]


def test_a_role_still_missing_is_put_back_first_from_the_cv():
    without = {"headline": "", "summary": "", "skills": [], "education": [], "other": [], "changes": [],
               "experience": [{"title": "Product Group Lead", "company": "Orbit Games", "dates": "2023-2025",
                               "bullets": ["Built a product group of 3 PMs and 4 engineering teams"]}]}
    res = tailor.tailor(CV2, _job2(), llm=lambda p: without)
    first = res["cv"]["experience"][0]
    assert (first["company"], first["title"], first["dates"]) == ("NOVA TLV, Israel", "Head of Product", "2025-Present")
    assert "Cut delivery cycles by 30%." in first["bullets"]
    assert any("NOVA" in f and "back" in f for f in res["flags"])


def test_an_employer_with_the_models_extra_words_is_kept_under_the_cvs_name():
    out = {"headline": "", "summary": "", "skills": [], "education": [], "other": [], "changes": [],
           "experience": [{"title": "Head of Product", "company": "Nova - sports streaming", "dates": "2025-Present",
                           "bullets": []},
                          {"title": "CPO", "company": "Google", "dates": "", "bullets": []},
                          {"title": "Product Group Lead", "company": "Orbit Games", "dates": "2023-2025", "bullets": []}]}
    res = tailor.tailor(CV2, _job2(), llm=lambda p: out)
    assert [r["company"] for r in res["cv"]["experience"]] == ["NOVA TLV, Israel", "Orbit Games"]
    assert any(r["what"] == "role" and "Google" in r["text"] for r in res["removed"])


def test_a_saved_draft_without_a_cv_role_gets_it_back_when_opened(stack, users, monkeypatch):
    """Eran, 2026-11-08: still no DAZN - on a draft saved before the role check."""
    import app as A
    monkeypatch.setattr(A, "_tailor_inputs", lambda uid, user: (CV2, "Dana", ["d@example.test"]))
    uid = _user_id(stack["db"], "alice@example.test")
    job_id = _new_job(stack["db"], uid, title="Chief Product Officer", company="OTI")
    old = {"cv": {"headline": "", "summary": "", "skills": [], "education": [], "other": [], "changes": [],
                  "experience": [{"title": "Product Group Lead", "company": "ORBIT GAMES TLV, Israel",
                                  "dates": "2023-2025", "bullets": []}]}, "removed": [], "flags": []}
    conn = stack["db"].get_db()
    conn.execute("INSERT INTO tailored_cvs (user_id, job_id, content, created_at) VALUES (?,?,?,?)",
                 (uid, job_id, json.dumps(old), "2026-11-08 14:50:00"))
    conn.commit()
    conn.close()
    st, _l, body = users["a"].get("/api/jobs/%d/tailored-cv" % job_id)
    got = json.loads(body)
    assert st == 200 and [r["company"] for r in got["cv"]["experience"]] == ["NOVA TLV, Israel", "ORBIT GAMES TLV, Israel"]
    assert any("back" in f for f in got["flags"])
    assert "NOVA" in A._tailored_cv_row(uid, job_id)["cv"]["experience"][0]["company"]   # saved back
    # A draft the user edited is theirs: not touched.
    edited = {**old, "cv": {**old["cv"], "experience": old["cv"]["experience"]}}
    conn = stack["db"].get_db()
    conn.execute("UPDATE tailored_cvs SET content=?, edited=1 WHERE user_id=? AND job_id=?", (json.dumps(edited), uid, job_id))
    conn.commit()
    conn.close()
    st, _l, body = users["a"].get("/api/jobs/%d/tailored-cv" % job_id)
    assert [r["company"] for r in json.loads(body)["cv"]["experience"]] == ["ORBIT GAMES TLV, Israel"]


# ── 2026-11-09: written for the job, measured changes, honest match ──────────

CV_FOCUS = """Experience
Head of Product - Northwind (2021 - Present)
- Owned the checkout roadmap for 4 markets.
- Built a pricing experimentation program with the data team.
- Hired and coached 6 product managers.
Product Manager - Contoso (2017 - 2021)
- Shipped the partner onboarding flow.
- Wrote the API documentation portal.
Product leader with a passion for checkout, pricing and onboarding. Built teams that ship.
Education
B.A. Economics (2016)
"""


def _answer(summary, match=None, northwind=None):
    return {"headline": "Product leader - checkout and pricing", "summary": summary,
            "skills": ["pricing", "experimentation", "onboarding"],
            "match": match or [],
            "experience": [
                {"title": "Head of Product", "company": "Northwind", "dates": "2021 - Present",
                 "bullets": northwind or ["Built a pricing experimentation program with the data team.",
                                          "Owned the checkout roadmap for 4 markets."]},
                {"title": "Product Manager", "company": "Contoso", "dates": "2017 - 2021",
                 "bullets": ["Shipped the partner onboarding flow."]}],
            "education": ["B.A. Economics (2016)"], "other": [],
            "changes": ["Rewrote everything for payments."]}       # the model's claim: ignored


def test_a_summary_copied_from_the_cv_is_sent_back_once():
    calls = []

    def llm(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return _answer("Product leader with a passion for checkout, pricing and onboarding. Built teams that ship.")
        return _answer("Checkout and pricing are where most of this role's work sits, and Northwind's "
                       "checkout roadmap across 4 markets covers both. A pricing experimentation program "
                       "built with the data team shows the experimentation the job asks for.")
    res = tailor.tailor(CV_FOCUS, JOB, llm=llm)
    assert len(calls) == 2 and "copies the CV's own sentences" in calls[1]
    assert tailor.copied_share(res["cv"]["summary"], CV_FOCUS) < tailor.COPIED
    assert "Summary written for this job." in res["cv"]["changes"]


def test_what_changed_is_measured_not_the_models_word():
    res = tailor.tailor(CV_FOCUS, JOB, llm=lambda p: _answer(
        "Checkout, pricing and onboarding experience across two companies, led from the roadmap down."))
    ch = res["cv"]["changes"]
    assert "Rewrote everything for payments." not in ch
    assert any(c.startswith("Northwind:") and "moved to the top" in c and "1 less relevant point left out" in c
               for c in ch), ch
    assert any(c.startswith("Contoso:") and "left out" in c for c in ch), ch
    assert "Skills led by: pricing, experimentation, onboarding." in ch


def test_match_evidence_the_cv_does_not_back_becomes_a_gap():
    res = tailor.tailor(CV_FOCUS, JOB, llm=lambda p: _answer(
        "Checkout, pricing and onboarding experience across two companies, led from the roadmap down.",
        match=[{"need": "Pricing experimentation", "evidence": "Built a pricing experimentation program with the data team"},
               {"need": "Payments compliance", "evidence": "Led PCI compliance for card payments"},
               {"need": "Team leadership", "evidence": "Hired and coached 9 product managers"},
               {"need": "Mobile apps", "evidence": ""}]))
    m = {x["need"]: x["evidence"] for x in res["match"]}
    assert m["Pricing experimentation"].startswith("Built a pricing")
    assert m["Payments compliance"] == ""             # not in the CV
    assert m["Team leadership"] == ""                 # 9 is not the CV's 6
    assert m["Mobile apps"] == ""
