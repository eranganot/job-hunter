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
                     "source": "greenhouse"})
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
