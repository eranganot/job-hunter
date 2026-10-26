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


def _ms(monkeypatch, answers):
    """MicrosoftAdapter with its cookie session replaced by scripted answers.
    answers: list of (status, body) for the API calls, in order."""
    import ingestion.adapters.bigtech as bt
    monkeypatch.setattr(bt.time, "sleep", lambda s: None)
    calls, queue = [], list(answers)

    def call(self, opener, url, accept):
        calls.append(url)
        if url == MicrosoftAdapter.WARMUP:
            return 200, "<html></html>"
        return queue.pop(0) if queue else (200, '{"data": {"positions": [], "count": 0}}')
    monkeypatch.setattr(MicrosoftAdapter, "_call", call)
    return MicrosoftAdapter().fetch(Q), calls


def test_microsoft_reads_the_eightfold_search(monkeypatch):
    body = (FX / "microsoft_pcsx.json").read_text()
    res, calls = _ms(monkeypatch, [(200, body)])
    assert res.status == AdapterStatus.OK and res.count == 2
    j = res.jobs[0]
    assert j.url.startswith("https://apply.careers.microsoft.com/careers/job/")
    assert "IL" in j.location and j.posted_at is not None
    assert calls[0] == MicrosoftAdapter.WARMUP           # cookies first


def test_microsoft_retries_a_cookieless_429_once(monkeypatch):
    # Observed 2026-10-26: the refusal sets the session cookies; the same call
    # repeated with them succeeds.
    body = (FX / "microsoft_pcsx.json").read_text()
    res, calls = _ms(monkeypatch, [(429, ""), (200, body)])
    assert res.status == AdapterStatus.OK and res.count == 2
    res, _ = _ms(monkeypatch, [(429, ""), (429, "")])
    assert res.status == AdapterStatus.ERROR and "HTTP 429" in res.message


def test_microsoft_keeps_cookies_between_calls():
    import http.cookiejar
    import urllib.request
    op = MicrosoftAdapter()._opener()
    assert any(isinstance(h, urllib.request.HTTPCookieProcessor) and isinstance(h.cookiejar, http.cookiejar.CookieJar)
               for h in op.handlers)


def test_a_moved_endpoint_is_an_error_not_an_empty_ok(monkeypatch):
    for cls in (GoogleAdapter, AppleAdapter, AmazonAdapter):
        _a, res = _run(cls, [(404, "")] * 4, monkeypatch)
        assert res.status == AdapterStatus.ERROR and "HTTP 404" in res.message, cls.__name__
    res, _ = _ms(monkeypatch, [(404, "")])
    assert res.status == AdapterStatus.ERROR and "HTTP 404" in res.message


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
