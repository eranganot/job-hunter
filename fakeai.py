"""Fake AI for staging load and E2E tests (2026-11-24, launch item C12).

JH_FAKE_AI=1 makes gemini.generate answer locally, in the shape each caller
expects, after a realistic delay - so a load test of hundreds of users costs
nothing and does not depend on Google. The ceilings and the usage ledger still
run (estimated token counts), so the test exercises them too.

It refuses to run in production: with RAILWAY_ENVIRONMENT_NAME=production the
flag is ignored and an ERROR is logged, so a copied variable can never turn
real users' answers into placeholders. Health shows `fake_ai` either way.
"""
import hashlib
import json
import os
import re
import time

_WARNED = [False]


def enabled() -> bool:
    if (os.environ.get("JH_FAKE_AI", "") or "").strip() != "1":
        return False
    if (os.environ.get("RAILWAY_ENVIRONMENT_NAME", "") or "").strip().lower() == "production":
        if not _WARNED[0]:
            _WARNED[0] = True
            print("[fake-ai] ERROR JH_FAKE_AI=1 is set in production - ignored; real Gemini answers")
        return False
    return True


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


def _prompt(body) -> tuple:
    if isinstance(body, (bytes, bytearray)):
        try:
            body = json.loads(bytes(body).decode("utf-8"))
        except ValueError:
            return "", None
    texts = []
    for c in (body or {}).get("contents", []) or []:
        for p in c.get("parts", []) or []:
            if isinstance(p, dict) and p.get("text"):
                texts.append(p["text"])
    schema = ((body or {}).get("generationConfig") or {}).get("responseSchema")
    return "\n".join(texts), schema


def _from_schema(schema, seed):
    t = (schema or {}).get("type", "STRING").upper()
    if t == "OBJECT":
        return {k: _from_schema(v, seed + i) for i, (k, v) in enumerate((schema.get("properties") or {}).items())}
    if t == "ARRAY":
        return [_from_schema(schema.get("items") or {}, seed + i) for i in range(2)]
    if t == "INTEGER":
        return seed % 31 + 10
    if t == "NUMBER":
        return round((seed % 1000) / 10.0, 1)
    if t == "BOOLEAN":
        return bool(seed % 4)          # mostly true
    if schema.get("enum"):
        return schema["enum"][seed % len(schema["enum"])]
    return "fake answer"


def _jobs_in(prompt: str) -> list:
    """The first JSON array of job objects (with a url) inside the prompt."""
    for m in re.finditer(r"\[\s*\{", prompt):
        depth, i = 0, m.start()
        for j in range(i, len(prompt)):
            ch = prompt[j]
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    try:
                        arr = json.loads(prompt[i:j + 1])
                    except ValueError:
                        break
                    if arr and isinstance(arr[0], dict) and "url" in arr[0]:
                        return arr
                    break
    return []


def _scored(prompt: str) -> list:
    out = []
    for j in _jobs_in(prompt):
        score = 20 + _seed(j.get("url"), j.get("job_title")) % 76       # 20-95
        if score < 30:
            continue
        out.append({"job_title": j.get("job_title", ""), "company": j.get("company", ""),
                    "location": j.get("location", ""), "url": j.get("url", ""), "publish_date": None,
                    "full_description": j.get("full_description", ""),
                    "description": (j.get("description") or "Fake AI description.")[:300],
                    "candidate_score": score,
                    "fit_reason": "Fake AI: your background lines up with this role's main requirements."})
    return out


_ANALYZE_CV = {
    "job_titles": ["Product Manager", "Senior Product Manager", "Group Product Manager",
                   "Product Lead", "Head of Product", "Product Owner"],
    "keywords": ["roadmap", "A/B testing", "SQL", "stakeholder management", "B2C", "analytics",
                 "user research", "experimentation"],
    "locations": ["Tel Aviv"], "experience_years": 8, "seniority": "senior",
    "summary": "Fake AI summary: an experienced professional in Israeli tech.",
    "recommendations": ["Fake tip one.", "Fake tip two.", "Fake tip three."],
    "score": 72, "score_label": "Good Profile",
    "linkedin_url": "", "phone": "", "full_name": "", "email": "",
}
_CV_OPTIMIZER = {"score": 72, "score_label": "Good", "summary": "Fake AI review.",
                 "strengths": ["Clear structure", "Relevant experience"],
                 "improvements": [{"title": "Quantify impact", "detail": "Fake AI detail."}],
                 "ats_notes": ["Fake ATS note."]}
_TAILORED = {"match": [{"need": "fake requirement", "evidence": ""}], "headline": "Fake AI headline",
             "summary": "Fake AI summary.", "skills": [], "experience": [], "education": [], "other": []}
_COVER = ("Dear hiring team,\n\nThis is a placeholder cover letter written by the fake AI used in "
          "staging tests.\n\nBest regards")


def answer(purpose: str, body) -> str:
    prompt, schema = _prompt(body)
    seed = _seed(purpose, len(prompt))
    if schema:
        return json.dumps(_from_schema(schema, seed))
    if purpose in ("search_scoring", "search_scoring_desc", "resume_search", "score"):
        return json.dumps(_scored(prompt))
    if purpose == "web_search":
        return "[]"
    if purpose == "analyze_cv":
        return json.dumps(_ANALYZE_CV)
    if purpose == "cv_optimizer":
        return json.dumps(_CV_OPTIMIZER)
    if purpose == "tailored_cv":
        return json.dumps(_TAILORED)
    if purpose == "cover_letter":
        return _COVER
    if "JSON array" in prompt:
        return "[]"
    if "JSON object" in prompt or "JSON:" in prompt:
        return "{}"
    return "Fake AI answer."


def latency_s(purpose: str) -> float:
    try:
        ms = int(os.environ.get("JH_FAKE_AI_LATENCY_MS", "1500") or 1500)
    except ValueError:
        ms = 1500
    if purpose in ("search_scoring", "search_scoring_desc"):
        ms *= 3                     # the real scoring calls are the slow ones
    return max(0, ms) / 1000.0


def respond(purpose: str, body) -> dict:
    """A Gemini-shaped result. Token counts are estimates (4 characters each)."""
    prompt, _schema = _prompt(body)
    text = answer(purpose, body)
    time.sleep(latency_s(purpose))
    return {"candidates": [{"content": {"parts": [{"text": text}], "role": "model"}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": len(prompt) // 4, "candidatesTokenCount": len(text) // 4,
                              "totalTokenCount": (len(prompt) + len(text)) // 4},
            "fake": True}
