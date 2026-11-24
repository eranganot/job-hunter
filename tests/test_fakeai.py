"""C12 (2026-11-24): JH_FAKE_AI=1 answers locally on staging, in the shape each
caller expects, still metered - and never in production."""
import json

import fakeai
import gemini


def _on(monkeypatch, env="staging"):
    monkeypatch.setenv("JH_FAKE_AI", "1")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", env)
    monkeypatch.setenv("JH_FAKE_AI_LATENCY_MS", "0")


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("JH_FAKE_AI", raising=False)
    assert fakeai.enabled() is False


def test_never_in_production(monkeypatch):
    _on(monkeypatch, env="production")
    assert fakeai.enabled() is False


def test_generate_uses_fake_and_is_metered(monkeypatch):
    _on(monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)       # no key needed
    gemini.reset_for_tests()
    before = gemini.usage()["global"]["calls"]
    text = gemini.generate_text({"contents": [{"parts": [{"text": "hello"}]}]}, purpose="unit")
    assert text == "Fake AI answer."
    assert gemini.usage()["global"]["calls"] == before + 1


def test_scoring_echoes_the_jobs_with_scores(monkeypatch):
    _on(monkeypatch)
    jobs = [{"job_title": "PM %d" % i, "company": "Co", "location": "TLV",
             "url": "https://x.example/%d" % i, "description": "d", "full_description": "f"} for i in range(30)]
    prompt = "Score these.\nJobs:\n" + json.dumps(jobs) + "\nReturn ONLY a valid JSON array"
    out = json.loads(fakeai.answer("search_scoring", {"contents": [{"parts": [{"text": prompt}]}]}))
    assert 0 < len(out) < 30
    assert all(30 <= j["candidate_score"] <= 95 and j["url"].startswith("https://x.example/") for j in out)
    again = json.loads(fakeai.answer("search_scoring", {"contents": [{"parts": [{"text": prompt}]}]}))
    assert again == out                                            # deterministic


def test_schema_answers_match_the_schema(monkeypatch):
    schema = {"type": "OBJECT", "properties": {
        "keyword_score": {"type": "INTEGER"}, "is_open": {"type": "BOOLEAN"},
        "confidence": {"type": "STRING", "enum": ["high", "medium", "low"]}}}
    out = json.loads(fakeai.answer("match_score", {"contents": [{"parts": [{"text": "x"}]}],
                                                   "generationConfig": {"responseSchema": schema}}))
    assert isinstance(out["keyword_score"], int) and isinstance(out["is_open"], bool)
    assert out["confidence"] in ("high", "medium", "low")


def test_fixed_shapes():
    cv = json.loads(fakeai.answer("analyze_cv", {"contents": []}))
    assert cv["job_titles"] and cv["seniority"] and "score" in cv
    assert json.loads(fakeai.answer("web_search", {})) == []
    assert "Dear" in fakeai.answer("cover_letter", {})
    assert "experience" in json.loads(fakeai.answer("tailored_cv", {}))
    assert json.loads(fakeai.answer("other", b'{"contents":[{"parts":[{"text":"Return ONLY a JSON array"}]}]}')) == []
