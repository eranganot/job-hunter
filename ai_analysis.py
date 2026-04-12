"""
ai_analysis.py — CV analysis via Claude API for Job Hunter
"""
import base64
import json
import os
import re
import urllib.request
import urllib.error


def analyze_cv(pdf_path: str, api_key: str) -> dict:
    """
    Analyze a CV/resume PDF using Claude and return structured job recommendations.

    Returns a dict with:
        job_titles       list[str]  — 6-8 relevant job titles to search for
        keywords         list[str]  — top 8-12 skills / technologies
        locations        list[str]  — preferred work locations
        salary_min       int        — monthly NIS (lower bound)
        salary_max       int        — monthly NIS (upper bound)
        experience_years int        — estimated years of experience
        seniority        str        — junior | mid | senior | director | executive
        summary          str        — 2-3 sentence profile
        recommendations  list[str]  — 3-4 job search tips
    """
    if not api_key:
        raise ValueError(
            "Anthropic API key not configured. "
            "Add it to config.json under 'anthropic_api_key'."
        )

    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"CV file not found: {pdf_path}")

    with open(pdf_path, "rb") as f:
        pdf_b64 = base64.b64encode(f.read()).decode()

    prompt = (
        "You are an expert career advisor and recruiter. "
        "Analyze this CV/resume carefully and return ONLY a raw JSON object "
        "(no markdown, no code fences, no explanation) with exactly these fields:\n\n"
        "{\n"
        '  "job_titles": ["6-8 specific job titles relevant to this person\'s background"],\n'
        '  "keywords": ["8-12 key skills, technologies, methodologies, or domain keywords"],\n'
        '  "locations": ["preferred work locations — default to Tel Aviv if unclear"],\n'
        '  "salary_min": <integer, estimated minimum monthly salary in NIS>,\n'
        '  "salary_max": <integer, estimated maximum monthly salary in NIS>,\n'
        '  "experience_years": <integer, total years of professional experience>,\n'
        '  "seniority": "<junior|mid|senior|director|executive>",\n'
        '  "summary": "2-3 sentences describing this person\'s professional profile",\n'
        '  "recommendations": ["3-4 specific, actionable job search tips for this person"]\n'
        "}\n\n"
        "For Israeli market salary benchmarks (monthly NIS):\n"
        "  Product Manager (5y+): 35,000-55,000\n"
        "  Senior PM / Group PM: 45,000-65,000\n"
        "  Head of Product / Director: 55,000-85,000\n"
        "  VP Product: 70,000-100,000\n"
        "  CPO: 90,000-140,000\n\n"
        "Be specific with job titles — use real market titles. "
        "Return ONLY the JSON object."
    )

    payload = {
        "model": "claude-sonnet-4-6",  # Sonnet: same quality as Opus for structured extraction, ~10x cheaper
        "max_tokens": 1024,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_b64,
                    }
                },
                {
                    "type": "text",
                    "text": prompt,
                }
            ]
        }]
    }

    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        method="POST"
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", "2023-06-01")

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        raise RuntimeError(f"Claude API error {e.code}: {error_body}")

    raw = result["content"][0]["text"].strip()

    # Strip markdown code fences if Claude included them
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1])  # drop first and last fence lines

    data = json.loads(raw)

    # Normalise / fill defaults
    data.setdefault("job_titles", [])
    data.setdefault("keywords", [])
    data.setdefault("locations", ["Tel Aviv"])
    data.setdefault("salary_min", 0)
    data.setdefault("salary_max", 0)
    data.setdefault("experience_years", 0)
    data.setdefault("seniority", "senior")
    data.setdefault("summary", "")
    data.setdefault("recommendations", [])

    return data


# ── Local scoring (no API needed) ─────────────────────────────────────────────

def _parse_json_list(raw) -> list:
    """Safely parse a JSON list stored as a string, or return raw list."""
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw or "[]")
    except Exception:
        return []




def _haiku_match_score(
    job_text: str,
    user_keywords: list,
    user_titles: list,
    seniority: str,
    job_title: str,
    api_key: str,
) -> "tuple[int, int, int] | None":
    """Use Claude Haiku for semantic job-fit scoring.

    Returns (kw_score_0_60, title_score_0_30, seniority_score_0_10)
    or None on any failure (caller falls back to keyword matching).
    """
    if not api_key:
        return None

    kw_sample  = ", ".join(user_keywords[:30]) or "N/A"
    ttl_sample = ", ".join(user_titles[:10]) or "N/A"

    prompt = (
        "You are evaluating a job posting fit for a candidate.\n\n"
        f"Candidate skills/keywords: {kw_sample}\n"
        f"Candidate target titles:   {ttl_sample}\n"
        f"Candidate seniority:       {seniority or 'not specified'}\n\n"
        f"Job title: {job_title}\n"
        f"Job text (first 1500 chars):\n{job_text[:1500]}\n\n"
        "Score on three dimensions:\n"
        "  keyword_score  0-60  semantic skills/tools match\n"
        "  title_score    0-30  job title relevance to candidate's targets\n"
        "  seniority_score 0-10 seniority level fit\n\n"
        'Return ONLY valid JSON: {"keyword_score":0,"title_score":0,"seniority_score":0}'
    )
    try:
        body = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "x-api-key":          api_key,
                "anthropic-version":  "2023-06-01",
                "content-type":       "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp_data = json.loads(resp.read())
        text = resp_data["content"][0]["text"].strip()
        m = re.search(r"\{.*?\}", text, re.DOTALL)
        scores = json.loads(m.group()) if m else {}
        return (
            min(60, max(0, int(scores.get("keyword_score", 0)))),
            min(30, max(0, int(scores.get("title_score", 0)))),
            min(10, max(0, int(scores.get("seniority_score", 0)))),
        )
    except Exception:
        return None

def compute_match_score(job: dict, user_profile: dict, api_key: str = "") -> int:
    """
    Compute 0-100 match score using Claude Haiku for semantic scoring.
    Falls back to keyword overlap (60/30/10) when the API is unavailable.

    Weights:
      60% — skills/keywords fit  (semantic or keyword overlap)
      30% — job title relevance  (semantic or string match)
      10% — seniority alignment  (semantic or keyword match)

    Returns 0 if the user has no profile data yet.
    """
    user_keywords = _parse_json_list(user_profile.get("keywords"))
    user_titles   = _parse_json_list(user_profile.get("job_titles"))

    if not user_keywords and not user_titles:
        return 0

    job_text = " ".join(filter(None, [
        job.get("title", ""),
        job.get("description", ""),
        job.get("why_relevant", ""),
    ])).lower()

    # ── Try Haiku semantic scoring first ────────────────────────────────────
    haiku_key = api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_KEY", "")
    job_title_lower = (job.get("title") or "").lower()
    seniority = (user_profile.get("seniority") or "").lower()
    haiku = _haiku_match_score(job_text, user_keywords, user_titles, seniority, job_title_lower, haiku_key)
    if haiku is not None:
        kw_score, title_score, seniority_score = haiku
        return min(100, max(0, kw_score + title_score + seniority_score))

    # 60% — keyword overlap (fallback)
    if user_keywords:
        hits = sum(1 for kw in user_keywords if kw.lower() in job_text)
        kw_score = (hits / len(user_keywords)) * 60
    else:
        kw_score = 0

    # 30% — title relevance
    title_score = 0
    job_title_lower = job.get("title", "").lower()
    for ut in user_titles:
        words = [w for w in re.split(r"\W+", ut.lower()) if len(w) > 3]
        if any(w in job_title_lower for w in words):
            title_score = 30
            break

    # 10% — seniority alignment (fallback)
    seniority_keywords = {
        "junior":    ["junior", "associate", "entry"],
        "mid":       ["mid", "intermediate"],
        "senior":    ["senior", "sr.", "lead", "principal", "staff"],
        "director":  ["director", "head of"],
        "executive": ["vp", "vice president", "cpo", "cto", "ceo", "chief"],
    }
    seniority_score = 0
    for kw in seniority_keywords.get(seniority, []):
        if kw in job_title_lower:
            seniority_score = 10
            break

    return min(100, max(0, int(kw_score + title_score + seniority_score)))


def compute_candidate_score(job: dict, user_profile: dict) -> int:
    """
    Compute 0-100 candidate strength score for this specific role.

    Builds on match_score and adds profile-quality bonuses:
      +5  if the user has a filled CV summary
      +5  if experience_years >= 5
      -10 if experience_years < 2 and job title contains 'senior'/'lead'
    """
    base = compute_match_score(job, user_profile)

    if user_profile.get("cv_summary"):
        base = min(100, base + 5)

    exp = int(user_profile.get("experience_years") or 0)
    if exp >= 5:
        base = min(100, base + 5)

    job_title_lower = job.get("title", "").lower()
    senior_keywords = ["senior", "sr.", "lead", "principal", "director", "head of", "vp"]
    if exp < 3 and any(kw in job_title_lower for kw in senior_keywords):
        base = max(0, base - 10)

    return min(100, max(0, base))


# ── Job status check (Claude API + URL fetch) ──────────────────────────────────

def check_job_status(job_url: str, job_title: str, job_company: str, api_key: str) -> dict:
    """
    Fetch the job URL and ask Claude (Haiku) whether the role is still open.

    Returns a dict:
        is_open    bool | None  — True=open, False=closed, None=unclear
        reason     str          — one-sentence explanation
        confidence str          — 'high' | 'medium' | 'low'
        status_check str        — 'open' | 'closed' | 'unknown'
    """
    if not api_key:
        raise ValueError("Anthropic API key not configured.")
    if not job_url:
        return {"is_open": None, "reason": "No URL provided.", "confidence": "low", "status_check": "unknown"}

    # Fetch the page
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            )
        }
        req = urllib.request.Request(job_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_html = resp.read().decode("utf-8", errors="replace")
        # Strip HTML tags and truncate
        page_text = re.sub(r"<[^>]+>", " ", raw_html)
        page_text = re.sub(r"\s+", " ", page_text).strip()
        page_text = page_text[:6000]
    except Exception as e:
        return {
            "is_open": None,
            "reason": f"Could not fetch URL: {e}",
            "confidence": "low",
            "status_check": "unknown",
        }

    prompt = (
        f'You are checking whether a job posting is still active.\n'
        f'Job: "{job_title}" at "{job_company}"\n'
        f'URL: {job_url}\n\n'
        f'Page content (truncated):\n{page_text}\n\n'
        'Return ONLY a JSON object with these exact fields:\n'
        '{"is_open": true/false/null, "reason": "one sentence", "confidence": "high/medium/low"}\n\n'
        'is_open: true if the job is still accepting applications, false if closed/filled/expired, '
        'null if the page does not contain enough info to decide.\n'
        'confidence: high=clear signal found, medium=indirect signal, low=could not determine.'
    )

    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 256,
        "messages": [{"role": "user", "content": prompt}],
    }

    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", "2023-06-01")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        raise RuntimeError(f"Claude API error {e.code}: {error_body}")

    raw = result["content"][0]["text"].strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1])

    data = json.loads(raw)
    is_open = data.get("is_open")
    data["status_check"] = "open" if is_open is True else ("closed" if is_open is False else "unknown")
    return data



def generate_cover_letter(job: dict, profile: dict, api_key: str = "") -> str:
    """Generate a concise, personalized cover letter using Claude Haiku 4.5.

    Focuses on 2-3 specific skill overlaps between the CV and JD.
    Max 250 words, professional yet conversational tone.
    """
    api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "[Error] No API key available for cover letter generation."

    cv_summary = (profile.get("cv_summary") or profile.get("cv_analyzed") or "")[:4000]
    jd_text = (job.get("full_description") or job.get("description") or job.get("title", ""))[:4000]
    job_title = job.get("title", "Unknown Role")
    company = job.get("company", "the company")

    system_prompt = (
        "You are a job-search assistant writing personalized cover letters. "
        "Write a cover letter in a professional yet conversational tone, "
        "no more than 250 words. Focus on 2-3 specific overlaps between the "
        "candidate's CV and the job description. Do not invent experience "
        "not present in the CV. Output plain text only, no markdown headers."
    )

    user_msg = (
        f"JOB TITLE: {job_title}\n"
        f"COMPANY: {company}\n\n"
        f"JOB DESCRIPTION:\n{jd_text}\n\n"
        f"CV SUMMARY:\n{cv_summary}"
    )

    try:
        body = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 600,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_msg}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp_data = json.loads(resp.read())
        return resp_data["content"][0]["text"].strip()
    except Exception as e:
        print(f"[cover-letter] Error: {e}")
        return f"[Error] Could not generate cover letter: {e}"
