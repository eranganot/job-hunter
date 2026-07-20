"""
SecretJobs authenticated adapter: env-gating + shape-tolerant parsing.
The real feed shape is private, so we lock the flexible mapper against a few
plausible envelopes rather than one fixed schema.
"""
import pytest
pytest.importorskip("pydantic")

from ingestion.adapters.secretjobs import SecretJobsAdapter, _find_job_list, _node
from ingestion.config import config as _config
from ingestion.models import SearchQuery


def _q():
    return SearchQuery(titles=["Product Manager"], locations=["Tel Aviv"],
                       keywords=[], existing_urls=set(), limit_per_source=10)


def test_skips_without_env(monkeypatch):
    monkeypatch.delenv("SECRETJOBS_JOBS_URL", raising=False)
    monkeypatch.delenv("SECRETJOBS_AUTH_VALUE", raising=False)
    res = SecretJobsAdapter().fetch(_q())
    assert res.status.value == "skipped_no_creds"


def test_find_job_list_variants():
    assert _find_job_list([{"title": "y"}])[0]["title"] == "y"
    assert _find_job_list({"data": {"jobs": [{"title": "x"}]}})[0]["title"] == "x"
    edges = _find_job_list({"results": {"edges": [{"node": {"title": "z"}}]}})
    assert _node(edges[0])["title"] == "z"


def test_parses_flexible_shapes(monkeypatch):
    monkeypatch.setattr(_config, "SECRETJOBS_JOBS_URL", "https://www.secretjobs.ai/api/feed")
    monkeypatch.setattr(_config, "SECRETJOBS_AUTH_VALUE", "cookie=abc")
    a = SecretJobsAdapter()
    payload = {"jobs": [
        {"jobTitle": "Senior PM", "companyName": "Monday.com", "city": "Tel Aviv",
         "applyUrl": "https://boards.greenhouse.io/monday/jobs/1",
         "datePosted": "2026-07-01", "id": "a1"},
        {"node": {"title": "Data Analyst", "company": {"name": "Lemonade"},
                  "url": "/he/job/2", "description": "Great role"}},
    ]}
    monkeypatch.setattr(a.http, "get_json", lambda url, **kw: payload)
    jobs = a._fetch(_q())
    assert len(jobs) == 2
    assert jobs[0].title == "Senior PM" and jobs[0].company == "Monday.com"
    assert jobs[0].url.endswith("/monday/jobs/1")
    assert jobs[1].company == "Lemonade"
    assert jobs[1].url == "https://www.secretjobs.ai/he/job/2"   # relative → absolute


def test_empty_or_junk_payload(monkeypatch):
    monkeypatch.setattr(_config, "SECRETJOBS_JOBS_URL", "https://www.secretjobs.ai/api/feed")
    monkeypatch.setattr(_config, "SECRETJOBS_AUTH_VALUE", "cookie=abc")
    a = SecretJobsAdapter()
    monkeypatch.setattr(a.http, "get_json", lambda url, **kw: None)
    assert a._fetch(_q()) == []
    monkeypatch.setattr(a.http, "get_json", lambda url, **kw: {"unexpected": True})
    assert a._fetch(_q()) == []
