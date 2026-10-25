"""Big-Tech adapters against payloads captured live on 2026-10-25.

Before this, Google (v3 API 404), Apple (/api/role/search -> /pagenotfound)
and Microsoft (old host failing TLS) returned [] with status OK for weeks.
Fixtures are trimmed real responses from the endpoints now used."""
import json
import pathlib

from ingestion.adapters.base import AdapterStatus
from ingestion.adapters.bigtech import AppleAdapter, GoogleAdapter, MicrosoftAdapter, AmazonAdapter
from ingestion.http_client import HttpResponse
from ingestion.models import SearchQuery

FX = pathlib.Path(__file__).parent / "fixtures" / "bigtech"
Q = SearchQuery(titles=["Product Manager"], locations=["Tel Aviv", "Israel"], limit_per_source=30)


class _Http:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, dict(params or {})))
        status, text = self.pages.pop(0) if self.pages else (200, "")
        return HttpResponse(status, text, url)


def _run(adapter_cls, pages, monkeypatch=None):
    a = adapter_cls()
    a.http = _Http(pages)
    if monkeypatch is not None:
        import ingestion.adapters.bigtech as bt
        monkeypatch.setattr(bt.time, "sleep", lambda s: None)
    return a, a.fetch(Q)


def test_google_reads_the_results_page():
    html = (FX / "google_results_page.html").read_text()
    a, res = _run(GoogleAdapter, [(200, html), (200, "<html>AF_initDataCallback</html>")])
    assert res.status == AdapterStatus.OK and res.count == 2
    j = res.jobs[0]
    assert j.title == "Group Product Manager, Filestore, Google Cloud"
    assert j.location == "Tel Aviv, Israel" and j.company == "Google"
    assert j.url.endswith("/jobs/results/118625963293450950-group-product-manager-filestore-google-cloud")
    assert a.http.calls[0][1]["location"] == "Israel"


def test_apple_reads_the_search_page_and_drops_per_location_repeats():
    html = (FX / "apple_search_page.html").read_text()
    a, res = _run(AppleAdapter, [(200, html), (200, "<html>__staticRouterHydrationData</html>")])
    assert res.status == AdapterStatus.OK
    # The captured page lists role 200690137 three times (Haifa, Israel, and
    # one more copy) - Apple repeats a role per location. One job comes out.
    ids = [j.source_job_id for j in res.jobs]
    assert ids == ["200690137"]
    assert res.jobs[0].url == "https://jobs.apple.com/en-us/details/200690137/design-engineer-for-soc-group"
    assert res.jobs[0].location == "Haifa"
    assert a.http.calls[0][1]["location"] == "israel-ISR"


def test_microsoft_reads_the_eightfold_search(monkeypatch):
    body = (FX / "microsoft_pcsx.json").read_text()
    a, res = _run(MicrosoftAdapter, [(200, body)], monkeypatch)
    assert res.status == AdapterStatus.OK and res.count == 2
    j = res.jobs[0]
    assert j.url.startswith("https://apply.careers.microsoft.com/careers/job/")
    assert "IL" in j.location and j.posted_at is not None


def test_a_moved_endpoint_is_an_error_not_an_empty_ok(monkeypatch):
    for cls in (GoogleAdapter, AppleAdapter, MicrosoftAdapter, AmazonAdapter):
        _a, res = _run(cls, [(404, "")] * 4, monkeypatch)
        assert res.status == AdapterStatus.ERROR and "HTTP 404" in res.message, cls.__name__


def test_a_changed_page_layout_is_an_error_not_an_empty_ok():
    for cls in (GoogleAdapter, AppleAdapter):
        _a, res = _run(cls, [(200, "<html><body>redesigned</body></html>")])
        assert res.status == AdapterStatus.ERROR and "layout changed" in res.message, cls.__name__


def test_source_health_remembers_each_source():
    import ingestion.integration as integ
    integ._remember("google", "ok", 14, 3)
    integ._remember("apple", "error", 0, 0, "HTTP 404 from jobs.apple.com")
    h = integ.source_health()
    assert h["google"]["kept"] == 3 and h["google"]["last_jobs_at"]
    assert h["apple"]["status"] == "error" and h["apple"]["last_ok_at"] is None
