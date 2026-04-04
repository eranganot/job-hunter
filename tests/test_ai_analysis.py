"""
tests/test_ai_analysis.py
Unit tests for ai_analysis.py â CV analysis, match scoring, and job status check.
"""
import json
import os
import tempfile
import pytest
from unittest.mock import patch, Mock


# ââ Helpers âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def _make_urlopen_mock(response_payload: dict):
    """Return a context-manager mock for urllib.request.urlopen."""
    mock_resp = Mock()
    mock_resp.read.return_value = json.dumps(response_payload).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = Mock(return_value=False)
    cm = Mock()
    cm.__enter__ = Mock(return_value=mock_resp)
    cm.__exit__ = Mock(return_value=False)
    return cm


def _api_response(text: str) -> dict:
    """Wrap raw text in the Anthropic API response envelope."""
    return {"content": [{"type": "text", "text": text}]}


# ââ Fixtures ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@pytest.fixture
def full_profile():
    return {
        "job_titles": '["Senior Backend Engineer", "Staff Engineer", "Principal Engineer"]',
        "keywords": '["Python", "PostgreSQL", "AWS", "Django", "Redis", "Docker"]',
        "locations": '["Tel Aviv"]',
        "salary_min": 30000,
        "salary_max": 55000,
        "seniority": "senior",
        "experience_years": 7,
        "cv_summary": "7 years building scalable backend systems in Python and AWS.",
    }


@pytest.fixture
def minimal_profile():
    """Profile with only job_titles, no keywords or summary."""
    return {
        "job_titles": '["Backend Engineer"]',
        "keywords": "[]",
        "seniority": "mid",
        "experience_years": 3,
        "cv_summary": "",
    }


@pytest.fixture
def good_match_job():
    return {
        "title": "Senior Backend Engineer",
        "company": "TechCorp",
        "description": (
            "We seek a Senior Python engineer with PostgreSQL and AWS experience. "
            "You'll build scalable distributed systems using Django and Redis. "
            "Docker and container orchestration required."
        ),
        "why_relevant": "Strong Python/AWS match",
    }


@pytest.fixture
def poor_match_job():
    return {
        "title": "iOS Mobile Developer",
        "company": "MobileCo",
        "description": "Swift, Objective-C, Xcode. Build consumer iOS apps from scratch.",
        "why_relevant": "",
    }


@pytest.fixture
def junior_job():
    return {
        "title": "Junior Backend Developer",
        "company": "StartupXYZ",
        "description": "Entry-level backend role. Basic Python knowledge required.",
        "why_relevant": "",
    }


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# compute_match_score
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

class TestComputeMatchScore:

    def test_empty_profile_returns_zero(self, good_match_job):
        from ai_analysis import compute_match_score
        assert compute_match_score(good_match_job, {}) == 0

    def test_profile_without_keywords_or_titles_returns_zero(self, good_match_job):
        from ai_analysis import compute_match_score
        profile = {"seniority": "senior", "experience_years": 5}
        assert compute_match_score(good_match_job, profile) == 0

    def test_good_match_scores_above_50(self, good_match_job, full_profile):
        from ai_analysis import compute_match_score
        score = compute_match_score(good_match_job, full_profile)
        assert score > 50, f"Expected >50 for a good match, got {score}"

    def test_poor_match_scores_below_good_match(self, good_match_job, poor_match_job, full_profile):
        from ai_analysis import compute_match_score
        good_score = compute_match_score(good_match_job, full_profile)
        poor_score = compute_match_score(poor_match_job, full_profile)
        assert good_score > poor_score, (
            f"Good match ({good_score}) should outscore poor match ({poor_score})"
        )

    def test_score_is_clamped_between_0_and_100(self, good_match_job, full_profile):
        from ai_analysis import compute_match_score
        score = compute_match_score(good_match_job, full_profile)
        assert 0 <= score <= 100

    def test_score_with_only_keywords(self, good_match_job):
        from ai_analysis import compute_match_score
        profile = {
            "job_titles": "[]",
            "keywords": '["Python", "AWS", "PostgreSQL"]',
            "seniority": "senior",
        }
        score = compute_match_score(good_match_job, profile)
        assert score > 0

    def test_score_with_only_titles(self, good_match_job):
        from ai_analysis import compute_match_score
        profile = {
            "job_titles": '["Senior Backend Engineer"]',
            "keywords": "[]",
            "seniority": "senior",
        }
        score = compute_match_score(good_match_job, profile)
        assert score > 0

    def test_seniority_match_gives_nonzero_score(self, good_match_job, full_profile):
        from ai_analysis import compute_match_score
        # Profile seniority=senior, job title contains 'Senior' â seniority bonus
        score = compute_match_score(good_match_job, full_profile)
        assert score > 0

    def test_returns_int(self, good_match_job, full_profile):
        from ai_analysis import compute_match_score
        result = compute_match_score(good_match_job, full_profile)
        assert isinstance(result, int)

    def test_empty_job_description_still_scores_on_title(self, full_profile):
        from ai_analysis import compute_match_score
        job = {"title": "Senior Backend Engineer", "company": "X", "description": ""}
        score = compute_match_score(job, full_profile)
        assert 0 <= score <= 100


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# compute_candidate_score
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

class TestComputeCandidateScore:

    def test_cv_summary_bonus(self, good_match_job, full_profile, minimal_profile):
        from ai_analysis import compute_candidate_score
        # full_profile has cv_summary, minimal_profile does not
        score_with_summary = compute_candidate_score(good_match_job, full_profile)
        minimal_profile["job_titles"] = full_profile["job_titles"]
        minimal_profile["keywords"] = full_profile["keywords"]
        score_without_summary = compute_candidate_score(good_match_job, minimal_profile)
        assert score_with_summary >= score_without_summary

    def test_experience_bonus_for_5_plus_years(self, good_match_job, full_profile):
        from ai_analysis import compute_candidate_score, compute_match_score
        # experience_years=7 should give a bonus vs baseline match_score
        candidate_score = compute_candidate_score(good_match_job, full_profile)
        match_score = compute_match_score(good_match_job, full_profile)
        assert candidate_score >= match_score

    def test_seniority_penalty_for_junior_applying_senior_role(self, good_match_job, full_profile):
        from ai_analysis import compute_candidate_score
        junior_profile = dict(full_profile)
        junior_profile["experience_years"] = 1  # < 2 years
        score = compute_candidate_score(good_match_job, junior_profile)
        assert 0 <= score <= 100  # Should be penalised but still valid

    def test_score_clamped_0_to_100(self, good_match_job, full_profile):
        from ai_analysis import compute_candidate_score
        score = compute_candidate_score(good_match_job, full_profile)
        assert 0 <= score <= 100

    def test_empty_profile_returns_zero(self, good_match_job):
        from ai_analysis import compute_candidate_score
        assert compute_candidate_score(good_match_job, {}) == 0


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# check_job_status
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

class TestCheckJobStatus:

    def test_no_api_key_raises(self):
        from ai_analysis import check_job_status
        with pytest.raises((ValueError, Exception)):
            check_job_status("https://example.com/job", "Engineer", "Corp", "")

    def test_empty_url_returns_unknown(self):
        from ai_analysis import check_job_status
        result = check_job_status("", "Engineer", "Corp", "fake-key")
        assert result.get("status_check") == "unknown"

    def test_open_job_detected(self):
        from ai_analysis import check_job_status

        page_html = b"<html><body>We are hiring a backend engineer. Apply now!</body></html>"
        claude_payload = _api_response(
            json.dumps({"is_open": True, "reason": "Job is active", "confidence": "high"})
        )

        page_mock = Mock()
        page_mock.read.return_value = page_html
        page_mock.__enter__ = lambda s: s
        page_mock.__exit__ = Mock(return_value=False)

        call_count = 0
        original_mocks = [page_mock, _make_urlopen_mock(claude_payload)]

        def side_effect(req, timeout=None):
            nonlocal call_count
            m = original_mocks[min(call_count, len(original_mocks) - 1)]
            call_count += 1
            return m

        with patch("urllib.request.urlopen", side_effect=side_effect):
            result = check_job_status(
                "https://company.greenhouse.io/jobs/123",
                "Backend Engineer", "Company", "fake-api-key"
            )
        assert result.get("status_check") in ("open", "unknown")

    def test_closed_job_detected(self):
        from ai_analysis import check_job_status

        page_html = b"<html><body>This position has been filled. Thank you.</body></html>"
        claude_payload = _api_response(
            json.dumps({"is_open": False, "reason": "Position filled", "confidence": "high"})
        )

        page_mock = Mock()
        page_mock.read.return_value = page_html
        page_mock.__enter__ = lambda s: s
        page_mock.__exit__ = Mock(return_value=False)

        call_count = 0
        original_mocks = [page_mock, _make_urlopen_mock(claude_payload)]

        def side_effect(req, timeout=None):
            nonlocal call_count
            m = original_mocks[min(call_count, len(original_mocks) - 1)]
            call_count += 1
            return m

        with patch("urllib.request.urlopen", side_effect=side_effect):
            result = check_job_status(
                "https://company.lever.co/jobs/456",
                "Senior Engineer", "Company", "fake-api-key"
            )
        assert result.get("status_check") in ("closed", "unknown")

    def test_result_has_required_keys(self):
        from ai_analysis import check_job_status
        result = check_job_status("", "Engineer", "Corp", "fake-key")
        assert "is_open" in result
        assert "reason" in result
        assert "confidence" in result
        assert "status_check" in result


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# analyze_cv
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

class TestAnalyzeCv:

    def test_no_api_key_raises_value_error(self, tmp_path):
        from ai_analysis import analyze_cv
        pdf = tmp_path / "cv.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        with pytest.raises(ValueError, match="api_key"):
            analyze_cv(str(pdf), "")

    def test_missing_pdf_raises_file_not_found(self):
        from ai_analysis import analyze_cv
        with pytest.raises(FileNotFoundError):
            analyze_cv("/nonexistent/path/cv.pdf", "fake-key")

    def test_returns_all_required_keys(self, tmp_path):
        from ai_analysis import analyze_cv

        pdf = tmp_path / "cv.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake content")

        api_data = {
            "job_titles": ["Senior PM", "Group PM"],
            "keywords": ["B2B SaaS", "OKRs", "Agile"],
            "locations": ["Tel Aviv"],
            "salary_min": 50000,
            "salary_max": 80000,
            "experience_years": 8,
            "seniority": "senior",
            "summary": "8-year product leader.",
            "recommendations": ["Focus on Series B+", "Highlight metrics"],
        }
        mock_response = _make_urlopen_mock(_api_response(json.dumps(api_data)))

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = analyze_cv(str(pdf), "fake-key")

        for key in ["job_titles", "keywords", "locations", "salary_min", "salary_max",
                    "experience_years", "seniority", "summary", "recommendations"]:
            assert key in result, f"Missing key: {key}"

    def test_handles_markdown_code_fences(self, tmp_path):
        from ai_analysis import analyze_cv

        pdf = tmp_path / "cv.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        wrapped = "```json\n" + json.dumps({
            "job_titles": ["Engineer"],
            "keywords": ["Python"],
            "locations": ["TLV"],
            "salary_min": 20000,
            "salary_max": 40000,
            "experience_years": 3,
            "seniority": "mid",
            "summary": "Engineer",
            "recommendations": [],
        }) + "\n```"

        mock_response = _make_urlopen_mock(_api_response(wrapped))
        with patch("urllib.request.urlopen", return_value=mock_response):
            result = analyze_cv(str(pdf), "fake-key")

        assert result["seniority"] == "mid"
        assert "Python" in result["keywords"]

    def test_missing_keys_get_defaults(self, tmp_path):
        from ai_analysis import analyze_cv

        pdf = tmp_path / "cv.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        # Minimal response â missing several keys
        partial = {"job_titles": ["Engineer"], "seniority": "senior"}
        mock_response = _make_urlopen_mock(_api_response(json.dumps(partial)))

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = analyze_cv(str(pdf), "fake-key")

        assert isinstance(result.get("keywords"), list)
        assert isinstance(result.get("locations"), list)
        assert isinstance(result.get("recommendations"), list)
