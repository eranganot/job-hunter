"""links.py - link classification and the Step 1a measuring report.

Hosts are the ones actually seen in Eran's queue (STATUS.md 2026-10-12 and
/api/health's last_apply), so a change to the lists that reclassifies a real
link fails here first."""
import sqlite3

import pytest

import links


@pytest.mark.parametrize("url,expected", [
    ("https://il.linkedin.com/jobs/view/4012345678", "board"),
    ("https://www.linkedin.com/jobs/view/senior-pm-at-acme-123", "board"),
    ("https://il.indeed.com/viewjob?jk=abc", "board"),
    ("https://www.drushim.co.il/job/123/", "board"),
    ("https://boards.greenhouse.io/wizinc/jobs/123", "direct_ats"),
    ("https://job-boards.greenhouse.io/payoneer/jobs/456", "direct_ats"),
    ("https://jobs.lever.co/melio/abc-def", "direct_ats"),
    ("https://jobs.ashbyhq.com/lightricks/1", "direct_ats"),
    ("https://www.comeet.com/jobs/unframe/A1.B2/pm/C3.D4", "direct_ats"),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternal/job/x", "direct_ats"),
    ("https://jobgether.com/offer/abc-chief-product-officer", "aggregator"),
    ("https://builtin.com/job/product-manager/123", "aggregator"),
    ("https://jobright.ai/jobs/info/abc", "aggregator"),
    ("https://vertexaisearch.cloud.google.com/grounding-api-redirect/AbC", "redirect"),
    ("https://careers.monday.com/position/123", "company"),
    ("https://www.wix.com/jobs/locations/tel-aviv/positions/123", "company"),
    ("", "none"),
    (None, "none"),
    ("not a url", "none"),
])
def test_classify(url, expected):
    assert links.classify(url) == expected


def test_needs_resolving_only_for_links_that_are_not_the_company_s():
    assert links.needs_resolving("https://il.linkedin.com/jobs/view/1")
    assert links.needs_resolving("https://jobgether.com/offer/x")
    assert links.needs_resolving("")
    assert not links.needs_resolving("https://jobs.lever.co/melio/abc")
    assert not links.needs_resolving("https://careers.monday.com/position/1")


def test_direct_hosts_never_exceed_what_the_apply_engine_submits_to():
    # "direct" in the report must mean what the engine can act on: every
    # engine-direct host is direct here (comeet.com is the one addition - the
    # posting pages live there while the API is on comeet.co).
    import apply_engine
    for h in apply_engine._ATS_DIRECT_HOSTS:
        assert links.classify(f"https://{h}/x/y") == "direct_ats", h


def _db(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, company TEXT, "
                 "location TEXT, url TEXT, source TEXT, status TEXT)")
    for r in rows:
        conn.execute("INSERT INTO jobs (user_id, title, company, location, url, source, status) "
                     "VALUES (?,?,?,?,?,?,?)", r)
    return conn


ROWS = [
    (1, "Senior PM", "Wiz", "Tel Aviv", "https://il.linkedin.com/jobs/view/1", "linkedin", "new"),
    (1, "Senior PM", "Wiz", "Tel Aviv", "https://il.indeed.com/viewjob?jk=1", "indeed", "new"),   # same pair
    (1, "Head of Product", "Nowhere Ltd", "Tel Aviv", "https://il.linkedin.com/jobs/view/2", "linkedin", "approved"),
    (1, "Group PM", "Melio", "Tel Aviv", "https://jobs.lever.co/melio/1", "ats", "new"),
    (1, "Director of Product", "Monday", "Tel Aviv", "https://careers.monday.com/p/1", "gemini", "new"),
    (1, "CPO", "Jobgether", "Remote", "https://jobgether.com/offer/1", "web", "new"),
    (1, "Old applied one", "Gong", "Tel Aviv", "https://il.linkedin.com/jobs/view/3", "linkedin", "applied"),
    (2, "Someone else's", "Wiz", "Tel Aviv", "https://il.linkedin.com/jobs/view/4", "linkedin", "new"),
]


def _stub(found):
    calls = []

    def resolver(company, title, location, use_llm):
        calls.append((company, title))
        return found.get(company)
    return resolver, calls


def test_report_counts_only_open_jobs_of_the_user():
    resolver, _ = _stub({})
    r = links.link_report(_db(ROWS), user_id=1, sample=0, resolver=resolver)
    assert r["open_jobs"] == 6                       # applied row and user 2 excluded
    assert r["by_type"]["board"] == 3
    assert r["by_type"]["direct_ats"] == 1
    assert r["by_type"]["company"] == 1
    assert r["by_type"]["aggregator"] == 1
    assert r["needing_a_direct_link"] == 4
    assert r["direct_share"] == round(100 * 2 / 6, 1)
    assert r["top_hosts"][0] == {"host": "il.linkedin.com", "type": "board", "jobs": 2}


def test_report_across_all_users():
    resolver, _ = _stub({})
    r = links.link_report(_db(ROWS), user_id=None, sample=0, resolver=resolver)
    assert r["open_jobs"] == 7 and r["scope"] == "all users"


def test_dry_run_tries_each_company_title_pair_once_and_counts_hits():
    resolver, calls = _stub({"Wiz": {"url": "https://boards.greenhouse.io/wizinc/jobs/9", "ats": "greenhouse",
                                     "matched_title": "Senior Product Manager", "score": 91.0}})
    r = links.link_report(_db(ROWS), user_id=1, sample=10, resolver=resolver)
    d = r["dry_run"]
    # Wiz/Senior PM appears twice (LinkedIn + Indeed) but is tried once; the
    # direct and company-site jobs are never tried; approved jobs go first,
    # then newest first.
    assert calls == [("Nowhere Ltd", "Head of Product"), ("Jobgether", "CPO"), ("Wiz", "Senior PM")]
    assert d["distinct_pairs_needing_link"] == 3
    assert d["tried"] == 3 and d["resolved"] == 1 and d["hit_rate"] == round(100 / 3, 1)
    assert d["by_ats"] == {"greenhouse": 1}
    assert d["resolved_examples"][0]["to"].startswith("https://boards.greenhouse.io/")
    assert {u["company"] for u in d["unresolved"]} == {"Nowhere Ltd", "Jobgether"}


def test_dry_run_respects_the_sample_and_survives_a_crashing_resolver():
    def boom(*_a):
        raise TimeoutError("ATS hung")
    r = links.link_report(_db(ROWS), user_id=1, sample=2, resolver=boom)
    d = r["dry_run"]
    assert d["tried"] == 2 and d["resolved"] == 0
    assert "TimeoutError" in d["unresolved"][0]["error"]


def test_dry_run_stops_at_its_time_budget():
    resolver, calls = _stub({})
    r = links.link_report(_db(ROWS), user_id=1, sample=10, budget_s=-1, resolver=resolver)
    assert r["dry_run"]["tried"] == 0 and r["dry_run"]["not_reached"] == 3 and calls == []


def test_report_never_writes():
    conn = _db(ROWS)
    before = conn.execute("SELECT id, url, status FROM jobs ORDER BY id").fetchall()
    resolver, _ = _stub({"Wiz": {"url": "https://boards.greenhouse.io/wizinc/jobs/9", "ats": "greenhouse"}})
    links.link_report(conn, user_id=1, sample=10, resolver=resolver)
    assert [tuple(r) for r in conn.execute("SELECT id, url, status FROM jobs ORDER BY id")] == \
        [tuple(r) for r in before]


def test_ats_lookups_are_cached_per_company(monkeypatch):
    import apply_engine as ae
    n = {"calls": 0}

    def fake_get(url, timeout=6):
        n["calls"] += 1
        return None
    monkeypatch.setattr(ae, "_ats_get", fake_get)
    ae._ats_postings_for_slug("wizinc")
    first = n["calls"]
    ae._ats_postings_for_slug("WizInc")
    assert first == 5 and n["calls"] == 5, "second lookup for the same company hit the network"


# ── 1b: resolve_open_jobs ───────────────────────────────────────────────────

import os
import tempfile
from datetime import datetime, timedelta, timezone


def _file_db(rows):
    path = os.path.join(tempfile.mkdtemp(prefix="jh-links-"), "t.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, company TEXT, "
                 "location TEXT, url TEXT, source TEXT, status TEXT, url_verified INTEGER, "
                 "source_url TEXT, link_type TEXT, link_resolved_at TEXT, link_attempts INTEGER DEFAULT 0, "
                 "link_note TEXT, UNIQUE(user_id, url))")
    for r in rows:
        conn.execute("INSERT INTO jobs (user_id, title, company, location, url, source, status) "
                     "VALUES (?,?,?,?,?,?,?)", r)
    conn.commit()
    conn.close()

    def get_db():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c
    return get_db


def _rows(get_db):
    c = get_db()
    try:
        return {r["id"]: dict(r) for r in c.execute("SELECT * FROM jobs")}
    finally:
        c.close()


WIZ = {"url": "https://boards.greenhouse.io/wizinc/jobs/9", "ats": "greenhouse",
       "matched_title": "Senior Product Manager", "score": 91.0}


def test_resolve_rewrites_board_links_of_open_jobs_and_keeps_the_original():
    get_db = _file_db(ROWS)
    resolver, _ = _stub({"Wiz": WIZ})
    counts = links.resolve_open_jobs(get_db, 1, resolver=resolver)
    rows = _rows(get_db)
    # Jobs 1 and 2 are the same Wiz posting, found on LinkedIn and on Indeed.
    # Exactly one of them gets the direct link (newest first: job 2); the other
    # keeps its board URL and says where the direct link went.
    won, lost = rows[2], rows[1]
    assert won["url"] == WIZ["url"]
    assert won["source_url"] == "https://il.indeed.com/viewjob?jk=1"
    assert won["link_type"] == "direct_ats" and won["url_verified"] == 1
    assert lost["url"] == "https://il.linkedin.com/jobs/view/1"
    assert lost["link_note"] == "direct link already on job #2"
    # Direct and company-site jobs are recorded, untouched.
    assert rows[4]["link_type"] == "direct_ats" and rows[4]["url"] == "https://jobs.lever.co/melio/1"
    assert rows[5]["link_type"] == "company"
    # Misses are recorded with an attempt count.
    assert rows[3]["link_type"] == "board" and rows[3]["link_attempts"] == 1
    assert rows[6]["link_type"] == "aggregator" and rows[6]["link_attempts"] == 1
    assert counts == {"examined": 6, "already_direct": 2, "resolved": 1, "unresolved": 2,
                      "duplicate": 1, "left_for_next_run": 0}


def test_resolve_never_touches_history_or_other_users():
    get_db = _file_db(ROWS)
    resolver, _ = _stub({"Wiz": WIZ, "Gong": WIZ})
    links.resolve_open_jobs(get_db, 1, resolver=resolver)
    rows = _rows(get_db)
    assert rows[7]["url"] == "https://il.linkedin.com/jobs/view/3" and rows[7]["link_type"] is None  # applied
    assert rows[8]["url"] == "https://il.linkedin.com/jobs/view/4" and rows[8]["link_type"] is None  # user 2


def test_resolve_does_not_retry_until_a_week_has_passed():
    get_db = _file_db(ROWS)
    resolver, calls = _stub({})
    t0 = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    links.resolve_open_jobs(get_db, 1, resolver=resolver, now=t0)
    first = len(calls)
    links.resolve_open_jobs(get_db, 1, resolver=resolver, now=t0 + timedelta(days=3))
    assert len(calls) == first, "an unresolved job was retried before the week was up"
    links.resolve_open_jobs(get_db, 1, resolver=resolver, now=t0 + timedelta(days=8))
    assert len(calls) == 2 * first
    assert _rows(get_db)[3]["link_attempts"] == 2


def test_resolve_does_not_look_again_at_jobs_already_direct():
    get_db = _file_db(ROWS)
    resolver, calls = _stub({"Wiz": WIZ})
    t0 = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    links.resolve_open_jobs(get_db, 1, resolver=resolver, now=t0)
    calls.clear()
    links.resolve_open_jobs(get_db, 1, resolver=resolver, now=t0 + timedelta(days=30))
    # The Wiz pair is asked about once more - for the LinkedIn copy, which is
    # still a board link - never again for the row that already went direct.
    assert calls.count(("Wiz", "Senior PM")) == 1
    assert _rows(get_db)[2]["link_resolved_at"].startswith("2026-10-25")


def test_resolve_survives_a_crashing_resolver_and_respects_its_limits():
    get_db = _file_db(ROWS)

    def boom(*_a):
        raise TimeoutError("ATS hung")
    counts = links.resolve_open_jobs(get_db, 1, resolver=boom, limit=2)
    assert counts["examined"] == 2 and counts["left_for_next_run"] == 4
    counts = links.resolve_open_jobs(get_db, 1, resolver=boom, budget_s=-1)
    assert counts["examined"] == 0 and counts["left_for_next_run"] == 4


def test_run_detail_reads_as_one_line():
    assert links.run_detail({"examined": 3, "resolved": 1, "already_direct": 1, "unresolved": 1,
                             "duplicate": 0, "left_for_next_run": 0}).startswith("links: 3 examined, 1 resolved")


def test_a_resolved_company_careers_link_is_recorded_as_company():
    get_db = _file_db(ROWS)
    wiz_site = {"url": "https://www.wiz.io/careers/job/4621534006/:title?gh_jid=4621534006",
                "ats": "greenhouse", "score": 95.0}
    resolver, _ = _stub({"Nowhere Ltd": wiz_site})
    links.resolve_open_jobs(get_db, 1, resolver=resolver)
    row = _rows(get_db)[3]
    assert row["url"] == wiz_site["url"] and row["link_type"] == "company"


# ── 1d: ranking penalty ─────────────────────────────────────────────────────

@pytest.mark.parametrize("job,expected", [
    ({"link_type": "board", "link_resolved_at": "2026-10-25 10:00:00"}, 5),
    ({"link_type": "aggregator", "link_resolved_at": "2026-10-25 10:00:00"}, 5),
    ({"link_type": "direct_ats", "link_resolved_at": "2026-10-25 10:00:00"}, 0),
    ({"link_type": "company", "link_resolved_at": "2026-10-25 10:00:00"}, 0),
    ({"link_type": None, "link_resolved_at": None}, 0),          # not examined yet: no penalty
    ({"link_type": "board", "link_resolved_at": None}, 0),
])
def test_link_penalty(job, expected):
    assert links.link_penalty(job) == expected


def test_sql_penalty_matches_the_python_one():
    conn = sqlite3.connect(":memory:")
    for job, expected in [({"link_type": "board", "link_resolved_at": "x"}, 5),
                          ({"link_type": "company", "link_resolved_at": "x"}, 0),
                          ({"link_type": "board", "link_resolved_at": None}, 0)]:
        got = conn.execute("SELECT " + links.LINK_PENALTY_SQL.replace("link_resolved_at", "?", 1)
                           .replace("link_type", "?", 1), (job["link_resolved_at"], job["link_type"])).fetchone()[0]
        assert got == expected == links.link_penalty(job)


# ── Step 2: link_audit ──────────────────────────────────────────────────────

def _pages(mapping):
    def fetch(url, timeout):
        status, final, body = mapping[url]
        return status, final or url, body
    return fetch


AUDIT_ROWS = [
    (1, "Applied Data Scientist Expert", "Wiz", "Tel Aviv", "https://www.wiz.io/careers/job/4621534006/x", "gemini", "new"),
    (1, "Senior Product Manager", "Wiz", "Tel Aviv", "https://www.wiz.io/careers/job/1111111111/pm", "gemini", "new"),
    (1, "Senior Product Manager", "Monday", "Tel Aviv", "https://monday.com/careers/123987", "gemini", "approved"),
    (1, "Group PM", "Melio", "Tel Aviv", "https://jobs.lever.co/melio/1", "ats", "new"),       # ATS: not audited
    (1, "PM", "Gong", "Tel Aviv", "https://il.linkedin.com/jobs/view/3", "linkedin", "new"),   # board: not audited
]
PAGES = {
    "https://www.wiz.io/careers/job/4621534006/x":
        (200, None, "<title>Applied Data Scientist Expert | Wiz Careers</title>" + "<p>applied data scientist expert</p>" * 50),
    "https://www.wiz.io/careers/job/1111111111/pm":
        (200, "https://www.wiz.io/careers/not-found", "<title>We couldn't find the role | Wiz Careers</title>"),
    "https://monday.com/careers/123987":
        (200, None, "<title>monday.com Work Platform | Made For Work</title><p>" + "x " * 900 + "manager</p>"),
}


def test_audit_reads_company_and_aggregator_links_and_reports_what_the_page_shows():
    r = links.link_audit(_db(AUDIT_ROWS), user_id=1, fetch=_pages(PAGES))
    assert r["candidates"] == 3 and r["audited"] == 3 and r["not_reached"] == 0
    by = {j["job_id"]: j for j in r["jobs"]}
    real, fake_wiz, fake_monday = by[1], by[2], by[3]
    assert real["verdict"] == "alive" and real["tokens_in_page_title"] == len(real["title_tokens"])
    assert fake_wiz["verdict"] == "dead" and "not-found" in fake_wiz["reason"]
    # The invented-slug shape: alive to the checker, front-page title, no job
    # title tokens in it. Reported, not judged - that rule waits for real data.
    assert fake_monday["verdict"] == "alive" and fake_monday["tokens_in_page_title"] == 0
    assert r["verdicts"] == {"alive": 2, "dead": 1}


def test_audit_never_writes():
    conn = _db(AUDIT_ROWS)
    before = [tuple(x) for x in conn.execute("SELECT * FROM jobs ORDER BY id")]
    links.link_audit(conn, user_id=1, fetch=_pages(PAGES))
    assert [tuple(x) for x in conn.execute("SELECT * FROM jobs ORDER BY id")] == before


def test_audit_survives_a_page_that_cannot_be_fetched():
    def boom(url, timeout):
        raise TimeoutError("slow site")
    r = links.link_audit(_db(AUDIT_ROWS), user_id=1, fetch=boom)
    assert r["audited"] == 3 and all(j["verdict"] == "unknown" and j["fetched"] is False for j in r["jobs"])
