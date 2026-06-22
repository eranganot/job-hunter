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
