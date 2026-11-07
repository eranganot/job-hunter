"""workday.py - verified Workday tenants as a job source and a link resolver.

Fixtures are trimmed from real answers of Workday's search API on 2026-11-05.
No test touches the network."""
import io
import json
import pathlib
import urllib.error

import pytest

import links
import workday

APP = pathlib.Path(__file__).resolve().parent.parent / "app.py"

NVIDIA = "nvidia.wd5/NVIDIAExternalCareerSite"

NVIDIA_ANSWER = {"total": 166, "jobPostings": [
    {"title": "Senior Product Manager – Host Networking Platforms", "locationsText": "2 Locations",
     "externalPath": "/job/Israel-Yokneam/Senior-Product-Manager---Host-Networking-Platforms_JR2019895"},
    {"title": "Senior Product Manager - Run:ai DSX OS", "locationsText": "Israel, Tel Aviv",
     "externalPath": "/job/Israel-Tel-Aviv/Senior-Product-Manager---Run-ai-DSX-OS_JR2029254"},
    {"title": "Senior Product Manager - Run:ai DSX OS", "locationsText": "US, CA, Santa Clara",
     "externalPath": "/job/US-CA-Santa-Clara/Senior-Product-Manager---Run-ai-DSX-OS_JR2029255"},
    {"title": "Mechanical Product Engineer", "locationsText": "Vietnam, Remote",
     "externalPath": "/job/Vietnam-Remote/Mechanical-Product-Engineer_JR2018369-1"},
]}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code, headers=None):
    return urllib.error.HTTPError("https://x", code, "err", headers or {}, io.BytesIO(b"{}"))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    workday._CACHE.clear()
    monkeypatch.setattr(workday.time, "sleep", lambda s: None)


def _serve(monkeypatch, answers):
    """answers: {tenant: answer-dict | int (HTTP error) | list of those, consumed in order}"""
    calls = []

    def urlopen(req, timeout=0):
        tenant = req.full_url.split("//")[1].split(".")[0]
        calls.append((tenant, json.loads(req.data)))
        a = answers.get(tenant, 404)
        if isinstance(a, list):
            a = a.pop(0)
        if isinstance(a, int):
            raise _http_error(a)
        return _Resp(json.dumps(a).encode())
    monkeypatch.setattr(workday.urllib.request, "urlopen", urlopen)
    return calls


# ── parsing ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,ref", [
    ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/Israel-Tel-Aviv/x_JR1", NVIDIA),
    ("https://amat.wd1.myworkdayjobs.com/en-US/External/job/Rehovot-ISR/PM_R1", "amat.wd1/External"),
    ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite", None),     # site page, not a posting
    ("https://il.linkedin.com/jobs/view/1", None),
])
def test_ref_of(url, ref):
    assert workday.ref_of(url) == ref


def test_search_builds_the_public_posting_url_and_reads_location_from_the_path(monkeypatch):
    calls = _serve(monkeypatch, {"nvidia": NVIDIA_ANSWER})
    posts = workday.search(NVIDIA, "Senior Product Manager Israel")
    assert calls[0][1]["searchText"] == "Senior Product Manager Israel"
    assert posts[0]["url"] == ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
                               "/job/Israel-Yokneam/Senior-Product-Manager---Host-Networking-Platforms_JR2019895")
    # "2 Locations" says nothing; the path's primary location does
    assert workday.is_israel(posts[0]["location"]) and not workday.is_israel(posts[2]["location"])
    workday.search(NVIDIA, "Senior Product Manager Israel")
    assert len(calls) == 1                                  # cached


def test_a_stale_site_id_is_an_error_not_an_empty_answer(monkeypatch):
    _serve(monkeypatch, {"nvidia": 404})
    with pytest.raises(workday.WorkdayError, match="HTTP 404 from nvidia"):
        workday.search(NVIDIA, "PM Israel")
    assert not workday._CACHE                               # a failure is never cached


def test_a_429_is_retried_after_a_pause(monkeypatch):
    calls = _serve(monkeypatch, {"nvidia": [429, NVIDIA_ANSWER]})
    assert len(workday.search(NVIDIA, "PM Israel")) == 4 and len(calls) == 2


def test_a_429_that_never_clears_is_an_error(monkeypatch):
    calls = _serve(monkeypatch, {"nvidia": 429})
    with pytest.raises(workday.WorkdayError, match="429"):
        workday.search(NVIDIA, "PM Israel")
    assert len(calls) == 3


# ── the job source ─────────────────────────────────────────────────────────

def test_source_keeps_israeli_matching_postings_once_and_reports_dead_tenants(monkeypatch):
    _serve(monkeypatch, {"nvidia": NVIDIA_ANSWER, "intel": {"total": 0, "jobPostings": []}})
    jobs, stats = workday.search_israel(["Senior Product Manager", "Product Manager"],
                                        title_match=lambda t: "product manager" in t.lower())
    assert [j["url"].rsplit("_", 1)[1] for j in jobs] == ["JR2019895", "JR2029254"]   # US + Vietnam dropped, no repeats
    assert all(j["company"] == "NVIDIA" for j in jobs)
    dead = len(workday.TENANTS) - 2
    assert len(stats["failed"]) == dead                      # every other tenant 404s in this fixture
    status, msg = workday.health_line(stats)
    assert status == "degraded" and msg.startswith("%d of %d tenants failed" % (dead, len(workday.TENANTS)))


def test_health_is_ok_only_when_every_tenant_answered():
    assert workday.health_line({"failed": {}, "tenants": 16}) == ("ok", "")
    assert workday.health_line({"failed": {"a": "x"}, "tenants": 1})[0] == "error"


def test_the_search_loop_no_longer_swallows_workday_failures():
    src = APP.read_text(encoding="utf-8")
    assert "_WORKDAY_TENANTS" not in src                    # the stale hard-coded list is gone
    assert "workday.search_israel(" in src and '_src_remember("workday"' in src


# ── the link resolver ──────────────────────────────────────────────────────

def test_verified_tenants_are_known_boards_under_every_alias():
    learned = links.learned_boards(_empty_db())
    assert ("workday", NVIDIA) in learned["nvidia"] and ("workday", NVIDIA) in learned["mellanox"]
    assert ("workday", "hpe.wd5/ACJobSite") in learned[links._norm_company("Hewlett Packard Enterprise")]


def test_an_israeli_job_resolves_to_the_israeli_posting_never_the_same_title_abroad(monkeypatch):
    import apply_engine
    _serve(monkeypatch, {"nvidia": NVIDIA_ANSWER})
    learned = {"nvidia": [("workday", NVIDIA)]}
    apply_engine.begin_lookup()
    hit = links.resolve_from_learned("NVIDIA", "Senior Product Manager - Run:ai DSX OS", "Tel Aviv, Israel", learned)
    assert hit["url"].endswith("_JR2029254") and hit["ats"] == "workday"
    assert not apply_engine.lookup_incomplete()

    us_only = {"total": 1, "jobPostings": [NVIDIA_ANSWER["jobPostings"][2]]}
    workday._CACHE.clear()
    _serve(monkeypatch, {"nvidia": us_only})
    assert links.resolve_from_learned("NVIDIA", "Senior Product Manager - Run:ai DSX OS", "Tel Aviv, Israel",
                                      learned) is None


def test_a_tenant_that_does_not_answer_leaves_the_lookup_incomplete(monkeypatch):
    import apply_engine
    _serve(monkeypatch, {"nvidia": 503})
    apply_engine.begin_lookup()
    assert links.resolve_from_learned("NVIDIA", "Senior PM", "Tel Aviv, Israel",
                                      {"nvidia": [("workday", NVIDIA)]}) is None
    assert apply_engine.lookup_incomplete()                  # retried next run, not penalised


def _empty_db():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, company TEXT, url TEXT)")
    return conn


def test_workdays_outage_page_is_named_not_reported_as_not_json(monkeypatch):
    """Sentry 2026-11-07: '14 of 16 tenants failed: not JSON'. Every wd1 tenant
    answered 200 with Workday's 'currently unavailable' page."""
    import io
    import workday as wdm

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    page = (b"<!DOCTYPE html><html><head><title>Workday is currently unavailable.</title></head>"
            b"<body>We are experiencing a service interruption.</body></html>")
    monkeypatch.setattr(wdm.urllib.request, "urlopen", lambda *a, **k: _Resp(page))
    try:
        wdm._post("intel.wd1/External", "PM", 20, 5)
    except wdm.WorkdayError as e:
        assert "Workday outage on wd1" in str(e) and "not JSON" not in str(e)
    else:
        raise AssertionError("an outage page must raise")
