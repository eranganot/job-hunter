"""P7 (2026-11-25): the scoring and web-search prompts use the user's own seniority.

Reproduced first: a QA Engineer saved as Mid-level was scored with
"Seniority level: Senior / Director / VP / Head-of" and searched as
"seniority: Senior / Lead" - the line was fixed for every user."""
import json
import socket

import boardsearch
import gemini
from tests.test_first_matches import _new_user, mails  # noqa: F401
from tests.test_routes import Client, stack, users  # noqa: F401

JOB = {"job_title": "QA Engineer", "title": "QA Engineer", "company": "Acme", "location": "Tel Aviv",
       "url": "https://boards.greenhouse.io/acme/jobs/1", "description": "QA", "full_description": "QA",
       "source": "greenhouse"}


def _prompts(stack, monkeypatch, email, seniority):
    app = stack["app"]
    c, uid = _new_user(stack, email)
    c.post_json("/api/save-profile", {"job_titles": ["QA Engineer"], "keywords": ["Selenium"],
                                      "locations": ["Tel Aviv"], "seniority": seniority})

    def no_net(*a, **k):
        raise OSError("no network in tests")
    monkeypatch.setattr(socket, "create_connection", no_net)
    monkeypatch.setattr(boardsearch, "search", lambda *a, **k: ([dict(JOB)], {
        "boards": 1, "asked": 1, "postings": 1, "by_ats": {}, "not_reached": 0, "seconds": 0}))
    sent = []
    monkeypatch.setattr(gemini, "generate", lambda *a, **k: sent.append(json.dumps([a, k], default=str)) or "[]")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    app.run_job_search(uid)
    return "\n".join(sent).replace("\\\\n", "\n").replace("\\n", "\n")


def test_mid_level_user_is_scored_as_mid_level(stack, mails, monkeypatch):
    p = _prompts(stack, monkeypatch, "sen-1@example.test", "Mid-level")
    assert "Seniority level: Mid-level" in p
    assert "Senior / Director / VP / Head-of" not in p
    assert "seniority: Mid-level" in p                 # the web-search context


def test_no_seniority_means_judge_from_the_cv_not_senior(stack, mails, monkeypatch):
    p = _prompts(stack, monkeypatch, "sen-2@example.test", "")
    assert "Seniority level: not stated - judge it from the CV" in p
    assert "Senior / Lead" not in p and "seniority: as in the CV" in p
