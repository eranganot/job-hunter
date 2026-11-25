"""Feedback round P2, P4, P5 (product review, 2026-11-24)."""
import json

from tests.test_first_matches import _me, _new_user, mails  # noqa: F401
from tests.test_routes import Client, stack, users  # noqa: F401

APP = open("web/src/App.tsx", encoding="utf-8").read()


def test_first_search_stage_follows_the_search(stack, mails):
    app = stack["app"]
    c, uid = _new_user(stack, "fr-1@example.test")
    c.post_json("/api/save-profile", {"job_titles": ["QA Engineer"]})
    c.post_json("/api/save-schedule", {"onboarding_complete": 1})
    assert _me(c)["first_search_stage"] == "queued"          # queued, not started
    app._search_stage(uid, "collect")
    assert _me(c)["first_search_stage"] == "collect"
    app._search_stage(uid, "score")
    assert _me(c)["first_search_stage"] == "score"
    app._search_stage(uid, None)                              # the search ended
    assert uid not in app._SEARCH_STAGE
    app._funnel(uid, "first_search")
    me = _me(c)
    assert me["first_search_running"] is False and me["first_search_stage"] is None


def test_search_stage_logs_each_stage_and_the_total(stack, monkeypatch):
    app = stack["app"]
    lines = []
    monkeypatch.setattr(app, "print", lambda *a, **k: lines.append(" ".join(map(str, a))), raising=False)
    app._search_stage(987654, "collect")
    app._search_stage(987654, "save")
    app._search_stage(987654, None)
    out = "\n".join(lines)
    assert "[search-timing] user 987654 collect" in out and "[search-timing] user 987654 total" in out


def test_waiting_screen_shows_steps_not_a_spinner():
    assert "<FirstSearchSteps stage={me?.first_search_stage} />" in APP
    for label in ("Searching today's openings", "Scoring each one against your CV", "Writing why each one fits you"):
        assert label in APP


def test_tap_opens_the_full_description():
    assert "onTap={onCardTap}" in APP and "Tap to read the full description" in APP


def test_copy_changes():
    assert 'info.tier === "beta" ? "Free"' in APP and "free for a limited time" not in APP
    assert '"Salary too low"' not in APP
    assert "not your original design" in APP
    assert "Add Job Hunter to your home screen" in open("web/src/InstallPrompt.tsx", encoding="utf-8").read()
    assert "only you can reset your password" in open("web/src/VerifyEmailBanner.tsx", encoding="utf-8").read()


def test_offers_checkbox_is_below_the_form(stack):
    page = Client(stack["port"]).get("/register")[2]
    page = page.decode() if isinstance(page, bytes) else page
    assert page.index('name="marketing"') > page.index("</form>")


def test_search_mark_logs_seconds_per_source(stack, monkeypatch):
    app = stack["app"]
    lines = []
    monkeypatch.setattr(app, "print", lambda *a, **k: lines.append(" ".join(map(str, a))), raising=False)
    app._search_mark(55555, "nothing-running")          # no search: silent
    app._search_stage(55555, "collect")
    app._search_mark(55555, "ats-boards")
    app._search_mark(55555, "linkedin")
    app._search_stage(55555, None)
    out = "\n".join(lines)
    assert "collect/nothing-running" not in out
    assert "user 55555 collect/ats-boards" in out and "user 55555 collect/linkedin" in out
    src = open("app.py", encoding="utf-8").read()
    for label in ("setup+discovery", "ats-boards", "techmap-csv", "workday", "linkedin", "external+dedup"):
        assert '_search_mark(user_id, "%s")' % label in src


def test_finished_searches_keep_their_step_timings(stack):
    app = stack["app"]
    app._SEARCH_TIMINGS.clear()
    app._search_stage(4242, "collect")
    app._search_mark(4242, "linkedin")
    app._search_stage(4242, "score")
    app._search_stage(4242, None)
    t = app._SEARCH_TIMINGS[-1]
    assert t["user"] == 4242 and [s[0] for s in t["steps"]] == ["collect/linkedin", "collect", "score"]
    for i in range(12):
        app._search_stage(i, "collect"); app._search_stage(i, None)
    assert len(app._SEARCH_TIMINGS) == 10
