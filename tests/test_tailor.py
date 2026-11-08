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
