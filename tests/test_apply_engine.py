"""Pure-function coverage for apply_engine.py (backlog #9)."""
import apply_engine as ae


class TestUrlDetection:
    def test_greenhouse(self):
        assert ae._is_greenhouse("https://boards.greenhouse.io/acme/jobs/123")
        assert ae._is_greenhouse("https://grnh.se/abc123")
        assert not ae._is_greenhouse("https://acme.com/careers")

    def test_lever(self):
        assert ae._is_lever("https://jobs.lever.co/acme/2f1a9b3c-1234-5678-9abc-def012345678")
        assert not ae._is_lever("https://acme.com/jobs")

    def test_workday(self):
        assert ae._is_workday("https://acme.myworkdayjobs.com/en-US/careers/job/Tel-Aviv/PM_R-1")
        assert not ae._is_workday("https://acme.com/jobs")

    def test_job_board_true(self):
        for u in (
            "https://www.linkedin.com/jobs/view/1",
            "https://indeed.com/viewjob?jk=1",
            "https://wellfound.com/jobs/1",   # regression: lstrip('www.') bug
            "https://il.indeed.com/x",
        ):
            assert ae._is_job_board(u), u

    def test_job_board_false(self):
        for u in ("https://acme.com/careers", "https://boards.greenhouse.io/acme/jobs/1"):
            assert not ae._is_job_board(u), u


class TestClassifyFailure:
    def test_all_branches(self):
        assert ae._classify_failure("reCAPTCHA challenge")[0] == "captcha"
        assert ae._classify_failure("Navigation timed out")[0] == "timeout"
        assert ae._classify_failure("Please sign in to continue")[0] == "login_wall"
        assert ae._classify_failure("This field is required")[0] == "form_validation"
        assert ae._classify_failure("Connection refused by host")[0] == "network_error"
        assert ae._classify_failure("something unexpected happened")[0] == "other"

    def test_uses_page_text(self):
        assert ae._classify_failure("", "enter the captcha to proceed")[0] == "captcha"


class TestAddFailureType:
    def test_success_has_no_failure_type(self):
        r = ae._add_failure_type({"success": True, "status": "submitted", "error": ""})
        assert r["apply_failure_type"] is None

    def test_failure_is_classified(self):
        r = ae._add_failure_type({"success": False, "status": "failed", "error": "Navigation timed out"})
        assert r["apply_failure_type"] == "timeout"
        assert r["apply_failure_detail"]


class TestExtractApplicantFallback:
    def test_empty_cv_returns_email_only(self):
        d = ae.extract_applicant_data("", "x@e.com")
        assert d["email"] == "x@e.com"
        assert d["full_name"] == ""
        assert d["years_experience"] == 0


class TestClaudeHelperIsGemini:
    def test_claude_calls_gemini_endpoint(self, monkeypatch):
        import json as _json
        import apply_engine as ae
        monkeypatch.setattr(ae, "GEMINI_KEY", "test-key")
        captured = {}

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self):
                return _json.dumps({"candidates": [{"content": {"parts": [{"text": "hello"}]}}]}).encode()

        def _fake_urlopen(req, timeout=90):
            captured["url"] = req.full_url
            return _Resp()

        monkeypatch.setattr(ae.urllib.request, "urlopen", _fake_urlopen)
        out = ae._claude("hi there")
        assert out == "hello"
        assert "generativelanguage.googleapis.com" in captured["url"]
        assert "anthropic" not in captured["url"].lower()


# ── Parked / expired-domain detection (regression for false "Verified" badge) ──
def test_looks_parked_godaddy_lander_stub():
    # GoDaddy/Sedo-style JS-redirect stub (what saasjobs.io actually serves)
    stub = ('<!DOCTYPE html><html><head><script>window.onload=function(){'
            'window.location.href="/lander"}</script></head></html>')
    assert ae._looks_parked("https://saasjobs.io", stub) is True


def test_looks_parked_host_match():
    assert ae._looks_parked("https://forsale.godaddy.com/forsale/x.io", "") is True


def test_looks_parked_for_sale_phrases():
    body = "The domain name example.com is for sale. Make an offer."
    assert ae._looks_parked("https://example.com", body) is True


def test_looks_parked_real_job_not_flagged():
    # A real posting that merely runs a script or mentions "for sale" once must NOT be flagged
    body = ('<html><body><h1>Senior PM</h1><p>Great role, apply now in Tel Aviv.</p>'
            '<script>var h=window.location.host</script></body></html>')
    assert ae._looks_parked("https://boards.greenhouse.io/acme/jobs/1", body) is False


def test_looks_closed_position_filled():
    body = "<html><body><h1>Senior PM</h1><p>This position has been filled.</p></body></html>"
    assert ae._looks_closed(body) is True


def test_looks_closed_no_longer_accepting():
    body = "<html><body>We are no longer accepting applications for this role.</body></html>"
    assert ae._looks_closed(body) is True


def test_looks_closed_real_job_not_flagged():
    body = "<html><body><h1>Senior PM</h1><p>Apply now in Tel Aviv. Great role.</p></body></html>"
    assert ae._looks_closed(body) is False


def test_looks_closed_ignores_script_text():
    # the phrase ONLY inside a <script> must not trigger (visible text only)
    body = ('<html><head><script>var x="no longer accepting applications"</script></head>'
            '<body><h1>Open role</h1><p>Apply now.</p></body></html>')
    assert ae._looks_closed(body) is False


def test_check_url_alive_flags_closed(monkeypatch):
    closed = "<html><body>This job has been filled.</body></html>"

    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, n=-1): return closed.encode()
        def geturl(self): return "https://boards.greenhouse.io/acme/jobs/1"

    monkeypatch.setattr(ae.urllib.request, "urlopen", lambda req, timeout=8: _Resp())
    assert ae.check_url_alive("https://boards.greenhouse.io/acme/jobs/1") is False


def test_check_url_alive_flags_parked(monkeypatch):
    stub = ('<html><head><script>window.location.href="/lander"</script></head>'
            '<body></body></html>')

    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, n=-1): return stub.encode()
        def geturl(self): return "https://saasjobs.io"

    monkeypatch.setattr(ae.urllib.request, "urlopen", lambda req, timeout=8: _Resp())
    assert ae.check_url_alive("https://saasjobs.io") is False


# ── Required-question auto-answer (Phase 3) ────────────────────────────────────
class _FakePage:
    def __init__(self, fields=None):
        self._fields = fields or []
        self.filled = {}
        self.selected = {}
        self.checked = []
    def evaluate(self, js):
        return self._fields
    def fill(self, sel, val):
        self.filled[sel] = val
    def select_option(self, sel, value=None, label=None):
        self.selected[sel] = value if value is not None else label
    def check(self, sel):
        self.checked.append(sel)


def test_collect_unfilled_required_dedups_and_caps():
    fields = [{"selector": "#a", "label": "A", "type": "text", "options": []},
              {"selector": "#a", "label": "A dup", "type": "text", "options": []},
              {"selector": "#b", "label": "B", "type": "select", "options": ["x", "y"]}]
    out = ae._collect_unfilled_required(_FakePage(fields))
    sels = [f["selector"] for f in out]
    assert sels == ["#a", "#b"]


def test_answer_and_fill_required_executes_actions(monkeypatch):
    page = _FakePage()
    monkeypatch.setattr(ae, "_claude_json", lambda *a, **k: [
        {"selector": "#work_auth", "action": "select", "value": "Yes"},
        {"selector": "#name", "action": "fill", "value": "Eran"},
        {"selector": "#eeo", "action": "check", "value": ""},
    ])
    n = ae._answer_and_fill_required(page, [{"selector": "#work_auth"}], {"full_name": "Eran"})
    assert n == 3
    assert page.filled.get("#name") == "Eran"
    assert page.selected.get("#work_auth") == "Yes"
    assert "#eeo" in page.checked


def test_answer_and_fill_required_bad_llm_output(monkeypatch):
    monkeypatch.setattr(ae, "_claude_json", lambda *a, **k: {"error": "nope"})
    assert ae._answer_and_fill_required(_FakePage(), [{"selector": "#x"}], {}) == 0


def test_finalize_recovers_from_validation_block(monkeypatch):
    # first verify = failed (blocked), second verify = confirmed after answering
    seq = iter([
        {"success": False, "status": "failed", "confirmation_text": "", "error": "blocked"},
        {"success": True, "status": "confirmed", "confirmation_text": "Thank you", "error": ""},
    ])
    monkeypatch.setattr(ae, "_verify_submission", lambda page: next(seq))
    monkeypatch.setattr(ae, "_collect_unfilled_required", lambda page: [{"selector": "#q"}])
    monkeypatch.setattr(ae, "_answer_and_fill_required", lambda page, f, a: 1)

    class _P:
        def click(self, sel, timeout=0): pass
    res = ae._finalize_after_submit(_P(), {}, ['button[type="submit"]'])
    assert res["status"] == "confirmed"
    assert "auto-answered 1" in res["confirmation_text"]


# ── Deterministic ATS resolver (steps 2–4: company → board → posting) ──────────
def test_company_slugs_derives_real_slug():
    s = ae._company_slugs("monday.com")
    assert "monday" in s
    assert "paloaltonetworks" in ae._company_slugs("Palo Alto Networks Inc")


def test_is_direct_ats_classifies():
    assert ae._is_direct_ats("https://boards.greenhouse.io/acme/jobs/1")
    assert ae._is_direct_ats("https://jobs.lever.co/acme/abc")
    assert not ae._is_direct_ats("https://www.linkedin.com/jobs/view/123")


def test_resolve_ats_application_matches(monkeypatch):
    def fake_get(url, timeout=6):
        if "greenhouse" in url and "/monday/" in url:
            return {"jobs": [
                {"title": "Senior Product Manager", "id": 99,
                 "absolute_url": "https://boards.greenhouse.io/monday/jobs/99",
                 "location": {"name": "Tel Aviv"}},
                {"title": "Data Engineer", "id": 98,
                 "absolute_url": "https://boards.greenhouse.io/monday/jobs/98",
                 "location": {"name": "Tel Aviv"}},
            ]}
        return None
    monkeypatch.setattr(ae, "_ats_get", fake_get)
    r = ae.resolve_ats_application("monday.com", "Senior Product Manager")
    assert r and r["ats"] == "greenhouse"
    assert r["url"].endswith("/monday/jobs/99")
    assert r["score"] >= 82


def test_resolve_ats_application_no_title_match(monkeypatch):
    def fake_get(url, timeout=6):
        if "greenhouse" in url and "/monday/" in url:
            return {"jobs": [{"title": "Warehouse Associate", "id": 1,
                              "absolute_url": "https://boards.greenhouse.io/monday/jobs/1",
                              "location": {}}]}
        return None
    monkeypatch.setattr(ae, "_ats_get", fake_get)
    assert ae.resolve_ats_application("monday.com", "Senior Product Manager") is None


def test_resolve_ats_application_needs_company_and_title():
    assert ae.resolve_ats_application("", "PM") is None
    assert ae.resolve_ats_application("Acme", "") is None
