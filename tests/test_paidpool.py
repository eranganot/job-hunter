"""paidpool.py - the shared paid-source pool and its free-tier guard (2026-11-09).

Eran: "make sure that you will not pass the free tier". What must hold:
the cap never exceeds the free allowance; spend is reserved before a call and
only fits under the cap; a 4xx gives the reservation back, a timeout keeps it;
the month is spread over its days; each source runs once a day; nothing is
fetched per user."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

import migrations
import paidpool

NOW = datetime(2026, 11, 9, 10, 0, 0, tzinfo=timezone.utc)      # 12:00 in Israel, 22 days left


@pytest.fixture
def conn(monkeypatch):
    for s in paidpool.SOURCES.values():
        for k in s["keys"]:
            monkeypatch.delenv(k, raising=False)
    for n in paidpool.SOURCES:
        monkeypatch.delenv("JH_POOL_CAP_%s" % n.upper(), raising=False)
    monkeypatch.delenv("JH_POOL_ALLOW_ABOVE_FREE", raising=False)
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migrations.run(c)
    c.execute("INSERT INTO users (id, name, email, password_hash, salt, created_date, role, plan) "
              "VALUES (1,'a','a@x.test','h','s','2026-11-01','user','beta')")
    c.execute("INSERT INTO users (id, name, email, password_hash, salt, created_date, role, plan) "
              "VALUES (2,'b','b@x.test','h','s','2026-11-01','user','free')")
    c.execute("INSERT INTO user_profiles (user_id, job_titles) VALUES (1, ?)",
              (json.dumps(["Head of Product", "Director of Product"]),))
    c.execute("INSERT INTO user_profiles (user_id, job_titles) VALUES (2, ?)",
              (json.dumps(["Data Analyst"]),))
    c.commit()
    yield c
    c.close()


class _NoClose:
    def __init__(self, c):
        self.c = c

    def __getattr__(self, n):
        return getattr(self.c, n)

    def close(self):
        pass


def _fake(jobs=None, exc=None, seen=None):
    def f(titles, limit):
        if seen is not None:
            seen.append((titles, limit))
        if exc:
            raise exc
        return (jobs or [])[:limit]
    return f


def _jobs(n, prefix="j"):
    return [{"provider_job_id": "%s%d" % (prefix, i), "title": "Head of Product %d" % i,
             "company": "Acme", "location": "Tel Aviv, Israel",
             "url": "https://acme.example/jobs/%s%d" % (prefix, i), "description": "", "posted_at": ""}
            for i in range(n)]


# ── the cap ────────────────────────────────────────────────────────────────

def test_caps_never_exceed_the_free_allowance(monkeypatch):
    assert paidpool.cap("theirstack") == 180 and paidpool.cap("hirebase") == 450
    monkeypatch.setenv("JH_POOL_CAP_THEIRSTACK", "5000")
    assert paidpool.cap("theirstack") == 200                      # clamped to the free 200
    monkeypatch.setenv("JH_POOL_ALLOW_ABOVE_FREE", "1")
    assert paidpool.cap("theirstack") == 5000                     # only with an explicit opt-in
    for s in paidpool.SOURCES.values():
        assert s["default_cap"] <= s["hard_limit"]


def test_reserve_refuses_anything_past_the_cap(conn):
    assert paidpool.reserve(conn, "theirstack", 170, NOW)
    assert not paidpool.reserve(conn, "theirstack", 11, NOW)      # 181 > 180
    assert paidpool.reserve(conn, "theirstack", 10, NOW)
    assert paidpool.spent(conn, "theirstack", NOW) == 180
    assert paidpool.budget_today(conn, "theirstack", NOW) == 0


def test_the_month_is_spread_over_its_days(conn):
    # 180 left, 22 days left in November (Israel) -> 8 today, not the whole cap
    assert paidpool.budget_today(conn, "theirstack", NOW) == 8
    assert paidpool.budget_today(conn, "hirebase", NOW) == 20


# ── one run ────────────────────────────────────────────────────────────────

def test_a_run_bills_only_what_came_back_and_fills_the_pool(conn, monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "k")
    seen = []
    res = paidpool.run_source(conn, "theirstack", NOW, {"theirstack": _fake(_jobs(3), seen=seen)})
    assert res["status"] == "ok" and res["fetched"] == 3 and res["added"] == 3
    assert seen[0][1] == 8                                        # asked for today's budget
    assert "Head of Product" in seen[0][0] and "Data Analyst" not in seen[0][0]   # Free user excluded
    assert paidpool.spent(conn, "theirstack", NOW) == 3           # reserved 8, settled to 3
    assert len(paidpool.jobs_for_search(conn, NOW)) == 3


def test_a_4xx_gives_the_reservation_back_a_timeout_keeps_it(conn, monkeypatch):
    monkeypatch.setenv("HIREBASE_API_KEY", "k")
    res = paidpool.run_source(conn, "hirebase", NOW,
                              {"hirebase": _fake(exc=paidpool.SourceError("HTTP 401 bad key", 401))})
    assert res["status"] == "error" and paidpool.spent(conn, "hirebase", NOW) == 0
    res = paidpool.run_source(conn, "hirebase", NOW,
                              {"hirebase": _fake(exc=paidpool.SourceError("TimeoutError: timed out"))})
    assert res["status"] == "error" and paidpool.spent(conn, "hirebase", NOW) == 20   # assume billed


def test_no_key_spends_nothing(conn):
    res = paidpool.run_source(conn, "theirstack", NOW, {"theirstack": _fake(_jobs(3))})
    assert res["status"] == "no_key" and paidpool.spent(conn, "theirstack", NOW) == 0


# ── the daily schedule ─────────────────────────────────────────────────────

def test_each_source_runs_once_a_day_and_not_before_six(conn, monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "k")
    calls = []
    fetchers = {n: _fake(_jobs(2, n), seen=calls) for n in paidpool.SOURCES}
    get = lambda: _NoClose(conn)
    early = datetime(2026, 11, 9, 2, 0, tzinfo=timezone.utc)      # 04:00 Israel
    assert paidpool.run_daily(get, early, fetchers) == {}
    out = paidpool.run_daily(get, NOW, fetchers)
    assert out["theirstack"]["status"] == "ok" and out["hirebase"]["status"] == "no_key"
    assert len(calls) == 1
    paidpool.run_daily(get, NOW, fetchers)                        # same day: nothing again
    assert len(calls) == 1
    # a key added later the same day starts that source on the next tick
    monkeypatch.setenv("HIREBASE_API_KEY", "k")
    paidpool.run_daily(get, NOW, fetchers)
    assert len(calls) == 2


def test_the_status_block_for_health(conn, monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "secret-value")
    paidpool.run_source(conn, "theirstack", NOW, {"theirstack": _fake(_jobs(2))})
    st = paidpool.status(conn, NOW)
    assert st["theirstack"]["key"] is True and st["theirstack"]["month_used"] == 2
    assert st["theirstack"]["month_cap"] == 180 and st["theirstack"]["free_allowance"] == 200
    assert "secret-value" not in json.dumps(st)


# ── each provider's payload, as documented ────────────────────────────────

def _stub_http(monkeypatch, payload, seen):
    def fake(method, url, headers=None, body=None, timeout=None):
        seen.append({"method": method, "url": url, "headers": headers, "body": body})
        return payload
    monkeypatch.setattr(paidpool, "_http", fake)


def test_theirstack_request_and_parse(monkeypatch):
    monkeypatch.setenv("THEIRSTACK_API_KEY", "tk")
    seen = []
    _stub_http(monkeypatch, {"data": [{"id": 7, "job_title": "Head of Product", "company": "Acme",
                                       "location": "Tel Aviv", "final_url": "https://acme.example/7",
                                       "date_posted": "2026-11-08"}]}, seen)
    out = paidpool._fetch_theirstack(["Head of Product"], 8)
    b = seen[0]["body"]
    assert b["limit"] == 8 and b["job_country_code_or"] == ["IL"] and b["posted_at_max_age_days"] == 3
    assert seen[0]["headers"]["Authorization"] == "Bearer tk"
    assert out[0]["url"] == "https://acme.example/7" and out[0]["provider_job_id"] == "7"


def test_hirebase_request_and_parse(monkeypatch):
    monkeypatch.setenv("HIREBASE_API_KEY", "hk")
    seen = []
    _stub_http(monkeypatch, {"jobs": [{"_id": "abc", "job_title": "Director of Product",
                                       "company_name": "Beta Ltd", "application_link": "https://beta.example/apply",
                                       "locations": [{"city": "Herzliya", "country": "Israel"}],
                                       "date_posted": "2026-11-07"}]}, seen)
    out = paidpool._fetch_hirebase(["Director of Product"], 20)
    b = seen[0]["body"]
    assert b["geo_locations"] == [{"country": "Israel"}] and b["limit"] == 20 and b["days_ago"] == 3
    assert seen[0]["headers"]["x-api-key"] == "hk"
    assert out[0] == {"provider_job_id": "abc", "title": "Director of Product", "company": "Beta Ltd",
                      "location": "Herzliya, Israel", "url": "https://beta.example/apply",
                      "description": "", "posted_at": "2026-11-07"}


def test_fantastic_request_and_parse(monkeypatch):
    monkeypatch.setenv("FANTASTIC_JOBS_API_KEY", "fk")
    seen = []
    _stub_http(monkeypatch, [{"id": 991, "title": "VP Product", "organization": "Gamma",
                              "url": "https://boards.greenhouse.io/gamma/1",
                              "locations_derived": ["Tel Aviv-Yafo, Israel"], "date_posted": "2026-11-09T05:00:00"}], seen)
    out = paidpool._fetch_fantastic(["VP Product", "Head of Product"], 15)
    u = seen[0]["url"]
    assert u.startswith("https://data.fantastic.jobs/v1/active-ats?")
    assert "time_frame=24h" in u and "location=Israel" in u and "limit=15" in u
    assert "%22Head+of+Product%22" in u                           # phrase-quoted, OR-joined
    assert seen[0]["headers"]["Authorization"] == "Bearer fk"
    assert out[0]["company"] == "Gamma" and out[0]["posted_at"] == "2026-11-09"


def test_fantastic_key_alias_is_accepted(monkeypatch):
    monkeypatch.delenv("FANTASTIC_JOBS_API_KEY", raising=False)
    monkeypatch.setenv("FANTASTIC_API_KEY", "alias")
    assert paidpool.api_key("fantastic") == "alias"


def test_apify_ethosia_request_and_parse(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "ak")
    seen = []
    _stub_http(monkeypatch, [{"stableJobId": "s1", "title": "Product Lead", "companyName": "Delta",
                              "location": "Tel Aviv", "url": "https://ethosia.co.il/job/1",
                              "applyUrl": "https://delta.example/careers/1", "postedDate": "2026-11-08"}], seen)
    out = paidpool._fetch_apify([], 30)
    assert "getascraper~ethosia-scraper/run-sync-get-dataset-items" in seen[0]["url"]
    assert "maxItems=30" in seen[0]["url"] and seen[0]["body"]["maxItems"] == 30
    assert seen[0]["headers"]["Authorization"] == "Bearer ak"
    assert out[0]["url"] == "https://delta.example/careers/1"     # the company's own page first


def test_pool_jobs_carry_their_real_source_name(conn, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "k")
    paidpool.run_source(conn, "apify", NOW, {"apify": _fake(_jobs(1, "e"))})
    assert paidpool.jobs_for_search(conn, NOW)[0]["source"] == "ethosia"
