"""P6 (2026-11-25): links are checked 24 at a time at insert, at most 6 at once per host."""
import socket
import threading
import time

import boardsearch
import linkcheck
from tests.test_first_matches import _new_user, mails  # noqa: F401
from tests.test_routes import Client, stack, users  # noqa: F401


def test_insert_link_check_is_wide_but_polite_per_host(stack, mails, monkeypatch):
    app = stack["app"]
    c, uid = _new_user(stack, "lc-1@example.test")
    c.post_json("/api/save-profile", {"job_titles": ["QA Engineer"], "keywords": ["Selenium"],
                                      "locations": ["Tel Aviv"], "seniority": "Mid-level"})
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError("off")))
    jobs = []
    for i in range(40):
        host = "boards.greenhouse.io" if i % 2 else "jobs.lever.co"
        jobs.append({"job_title": "QA Engineer", "title": "QA Engineer", "company": "Co%d" % i,
                     "location": "Tel Aviv", "url": "https://%s/co%d/jobs/%d" % (host, i, i),
                     "description": "QA Engineer, Selenium", "full_description": "QA Engineer, Selenium",
                     "source": "web_search"})   # not from a live board listing: checked
    monkeypatch.setattr(boardsearch, "search", lambda *a, **k: (list(jobs), {
        "boards": 1, "asked": 1, "postings": 40, "by_ats": {}, "not_reached": 0, "seconds": 0}))
    monkeypatch.setenv("JH_FAKE_AI", "1")
    monkeypatch.setenv("JH_FAKE_AI_LATENCY_MS", "0")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    lock = threading.Lock()
    now = {"all": 0, "max_all": 0}
    per = {}
    peak = {}

    def slow_classify(url, timeout=12, fetch=None):
        if timeout != 12:                  # the later sweeps (timeout 10) are not the insert check
            return "alive", "ok", url
        host = url.split("/")[2]
        with lock:
            now["all"] += 1; per[host] = per.get(host, 0) + 1
            now["max_all"] = max(now["max_all"], now["all"]); peak[host] = max(peak.get(host, 0), per[host])
        time.sleep(0.15)
        with lock:
            now["all"] -= 1; per[host] -= 1
        return "alive", "ok", url
    monkeypatch.setattr(linkcheck, "classify", slow_classify)
    app.run_job_search(uid)
    assert peak, "the link check never ran"
    assert max(peak.values()) <= app.LINK_CHECK_PER_HOST == 6
    assert now["max_all"] > 8          # wider than the old 8 when there are several hosts


def test_links_just_listed_on_their_own_board_are_not_fetched_again(stack, mails, monkeypatch):
    """Measured 2026-11-25: 413 links checked before the first picks, 249 on Greenhouse,
    32 s. A posting fetched from its board's API in this search, or listed on a board in
    the last 30 minutes, is not fetched again; everything else still is."""
    app = stack["app"]
    c, uid = _new_user(stack, "lc-2@example.test")
    c.post_json("/api/save-profile", {"job_titles": ["QA Engineer"], "keywords": ["Selenium"],
                                      "locations": ["Tel Aviv"], "seniority": "Mid-level"})
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError("off")))
    base = {"job_title": "QA Engineer", "title": "QA Engineer", "location": "Tel Aviv",
            "description": "QA Engineer, Selenium", "full_description": "QA Engineer, Selenium"}
    live_api = dict(base, company="A", url="https://boards.greenhouse.io/a/jobs/1", source="greenhouse")
    board_fresh = dict(base, company="B", url="https://jobs.lever.co/b/2", source="board-lever")
    board_old = dict(base, company="C", url="https://jobs.lever.co/c/3", source="board-lever")
    web = dict(base, company="D", url="https://boards.greenhouse.io/d/jobs/4", source="web_search")
    monkeypatch.setattr(boardsearch, "search", lambda *a, **k: ([dict(j) for j in (live_api, board_fresh, board_old, web)], {
        "boards": 1, "asked": 1, "postings": 4, "by_ats": {}, "not_reached": 0, "seconds": 0}))
    monkeypatch.setitem(boardsearch._LISTED_AT, board_fresh["url"], time.monotonic() - 60)
    monkeypatch.setitem(boardsearch._LISTED_AT, board_old["url"], time.monotonic() - 3 * 3600)
    monkeypatch.setenv("JH_FAKE_AI", "1")
    monkeypatch.setenv("JH_FAKE_AI_LATENCY_MS", "0")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    checked = []

    def rec(url, timeout=12, fetch=None):
        if timeout == 12:
            checked.append(url)
        return "alive", "ok", url
    monkeypatch.setattr(linkcheck, "classify", rec)
    app.run_job_search(uid)
    assert board_old["url"] in checked and web["url"] in checked
    assert live_api["url"] not in checked and board_fresh["url"] not in checked


def test_listed_recently_window():
    boardsearch._LISTED_AT["https://x.test/1"] = time.monotonic() - 10
    boardsearch._LISTED_AT["https://x.test/2"] = time.monotonic() - 3600
    assert boardsearch.listed_recently("https://x.test/1")
    assert not boardsearch.listed_recently("https://x.test/2") and not boardsearch.listed_recently("https://x.test/3")
