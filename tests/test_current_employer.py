"""Never recommend a job at the user's current employer (product review,
2026-11-24, feedback #4 - current employer only)."""
import json

import db as database
from tests.test_first_matches import _new_user, mails  # noqa: F401
from tests.test_routes import Client, stack, users  # noqa: F401


def test_company_key_folds_legal_suffixes_and_punctuation():
    k = database.company_key
    assert k("Wix.com Ltd.") == k("wix com") == "wix"
    assert k("monday.com") == k("Monday.com Ltd") == "monday"
    assert k("Check Point Software Technologies Ltd") == "check point software"
    assert k("Playtika Israel") == "playtika"
    assert k("Group") == "group"                 # a one-word name is never emptied
    assert k("") == "" and k(None) == ""
    assert k("Wix") != k("Wiz")                  # equality only, no fuzzy matching


def test_cv_current_employer_goes_on_the_blocklist(stack, mails, monkeypatch):
    app = stack["app"]
    c, uid = _new_user(stack, "ce-1@example.test")
    monkeypatch.setattr(app, "GEMINI_KEY", "test-key", raising=False)
    monkeypatch.setattr(app, "_cv_file", lambda *_a, **_k: "/tmp/cv.pdf")
    cv = {"job_titles": ["QA Engineer"], "keywords": ["Selenium"], "locations": ["Tel Aviv"],
          "summary": "s", "current_employer": "Monday.com Ltd"}
    monkeypatch.setattr(app, "analyze_cv", lambda *_a, **_k: dict(cv))
    out = json.loads(c.post_json("/api/analyze-cv", {})[2])
    assert out["current_employer"] == "Monday.com Ltd"
    conn = stack["db"].get_db()
    try:
        assert database.get_blocklist(conn, uid) == ["Monday.com Ltd"]
    finally:
        conn.close()


def test_no_current_employer_blocks_nothing(stack, mails, monkeypatch):
    app = stack["app"]
    c, uid = _new_user(stack, "ce-2@example.test")
    monkeypatch.setattr(app, "GEMINI_KEY", "test-key", raising=False)
    monkeypatch.setattr(app, "_cv_file", lambda *_a, **_k: "/tmp/cv.pdf")
    cv = {"job_titles": ["QA Engineer"], "keywords": [], "locations": ["Tel Aviv"], "summary": "s",
          "current_employer": ""}
    monkeypatch.setattr(app, "analyze_cv", lambda *_a, **_k: dict(cv))
    c.post_json("/api/analyze-cv", {})
    conn = stack["db"].get_db()
    try:
        assert database.get_blocklist(conn, uid) == []
    finally:
        conn.close()


def test_the_search_skips_blocked_companies_by_key():
    src = open("app.py", encoding="utf-8").read()
    assert "database.company_key(_jc) in _block_set" in src
    assert "{database.company_key(c) for c in database.get_blocklist(conn, user_id)}" in src


def test_the_cv_prompt_asks_for_the_current_employer_only():
    src = open("ai_analysis.py", encoding="utf-8").read()
    assert '"current_employer"' in src and "holds NOW" in src
