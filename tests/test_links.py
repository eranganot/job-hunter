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
