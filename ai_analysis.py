"""
ai_analysis.py — CV analysis via Gemini 2.5 Flash (Claude fallback) for Job Hunter
"""
import base64
import json
import os
import re
import urllib.request
import urllib.error

import gemini

# Route this module's print() calls through the logging module: timestamps,
# levels, the module name, and the request id of the request in flight.
import log as _log
print = _log.make_print(__name__)  # noqa: A001 - see log.py



def analyze_cv(pdf_path: str, api_key: str = "", user_id: int | None = None) -> dict:
    """
    Analyze a CV/resume PDF using Gemini 2.5 Flash (Claude Sonnet fallback).

    Returns a dict with:
        job_titles       list[str]  — 6-8 relevant job titles to search for
        keywords         list[str]  — top 8-12 skills / technologies
        locations        list[str]  — preferred work locations
        experience_years int        — estimated years of experience
        seniority        str        — junior | mid | senior | director | executive
        summary          str        — 2-3 sentence profile
        recommendations  list[str]  — 3-4 job search tips
        score            int        — overall profile strength 0-100
        score_label      str        — e.g. "Strong Profile"
        linkedin_url     str        — LinkedIn URL if present in CV, else ""
        phone            str        — phone number if present in CV, else ""
        full_name        str        — the person's name as written in the CV, else ""
        email            str        — email address if present in CV, else ""
    """
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
        '  "experience_years": <integer, total years of professional experience>,\n'
        '  "seniority": "<junior|mid|senior|director|executive>",\n'
        '  "summary": "2-3 sentences describing this person\'s professional profile",\n'
        '  "recommendations": ["3-4 specific, actionable job search tips for this person"],\n'
        '  "score": <integer 0-100, overall CV/profile strength for job hunting>,\n'
        '  "score_label": "<Weak Profile|Average Profile|Good Profile|Strong Profile|Excellent Profile>",\n'
        '  "linkedin_url": "<LinkedIn profile URL found in CV, or empty string if not present>",\n'
        '  "phone": "<phone number found in CV, or empty string if not present>",\n'
        '  "full_name": "<the person\'s full name as written in the CV, or empty string>",\n'
        '  "email": "<email address found in CV, or empty string if not present>"\n'
        "}\n\n"
        "Score guidance: 0-40 weak, 41-60 average, 61-75 good, 76-90 strong, 91-100 excellent. "
        "Score based on: clarity, relevance of experience, skills depth, career progression, "
        "and searchability for the Israeli/global tech market. "
        "For linkedin_url, phone, full_name and email: extract exactly as written in the CV, never invent them. "
        "Be specific with job titles — use real market titles. "
        "Return ONLY the JSON object."
    )

    # api_key param accepts either the explicit Gemini key or env-loaded one
    gemini_key = api_key or os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GEMINI_KEY", "")

    def _normalise(data: dict) -> dict:
        data.setdefault("job_titles", [])
        data.setdefault("keywords", [])
        data.setdefault("locations", ["Tel Aviv"])
        data.setdefault("experience_years", 0)
        data.setdefault("seniority", "senior")
        data.setdefault("summary", "")
        data.setdefault("recommendations", [])
        data.setdefault("score", 0)
        data.setdefault("score_label", "")
        data.setdefault("linkedin_url", "")
        data.setdefault("phone", "")
        data.setdefault("full_name", "")
        data.setdefault("email", "")
        # Remove salary fields if returned (no longer part of this response)
        data.pop("salary_min", None)
        data.pop("salary_max", None)
        return data

    def _strip_fences(raw: str) -> str:
        if raw.startswith("```"):
            lines = raw.split("\n")
            return "\n".join(lines[1:-1])
        return raw

    # ── 1. Gemini 2.5 Flash (primary) ────────────────────────────────────────
    if gemini_key:
        body = json.dumps({
            "contents": [{
                "parts": [
                    {"inlineData": {"mimeType": "application/pdf", "data": pdf_b64}},
                    {"text": prompt},
                ]
            }],
            "generationConfig": {
                "thinkingConfig": {"thinkingBudget": 0},
                "maxOutputTokens": 1536,
                "temperature": 0.1,
            },
        }).encode()
        try:
            result = gemini.generate(body, purpose="analyze_cv", user_id=user_id,
                                     key=gemini_key, timeout=90)
            raw = result["candidates"][0]["content"]["parts"][0]["text"].strip()
            raw = _strip_fences(raw)
            print("[analyze-cv] Done via Gemini 2.5 Flash")
            return _normalise(json.loads(raw))
        except gemini.BudgetExceeded:
            raise
        except Exception as e:
            raise RuntimeError(f"Gemini error: {e}")

    # Anthropic fallback removed — project is Gemini-only per request 2026-05-27.
    raise ValueError("GEMINI_API_KEY not configured. Set it in Railway environment.")


# ── Local scoring (no API needed) ─────────────────────────────────────────────

def _parse_json_list(raw) -> list:
    """Safely parse a JSON list stored as a string, or return raw list."""
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw or "[]")
    except Exception:
        return []




def _feedback_notes(signals: "dict | None") -> str:
    """Render a short natural-language summary of the user's pass history for the
    AI scorer. This is the AI half of the hybrid learning loop: it lets Gemini
    catch fuzzy patterns (e.g. crypto shops, agencies, low-salary tells) that the
    deterministic penalty rules can't express. Returns "" when there's nothing to
    learn from yet."""
    if not signals:
        return ""
    parts = []
    rc = signals.get("reason_counts") or {}
    if rc:
        top = ", ".join(f"{k} (x{v})" for k, v in list(rc.items())[:5])
        parts.append("Recurring reasons they pass on jobs: " + top + ".")
    ex = signals.get("examples") or []
    if ex:
        lines = []
        for e in ex[:8]:
            seg = f"{e.get('title','')} @ {e.get('company','')}".strip(" @")
            if e.get("reason"):
                seg += f" (reason: {e['reason']})"
            if seg:
                lines.append(seg)
        if lines:
            parts.append("Roles they recently rejected: " + "; ".join(lines) + ".")
    liked = sorted(signals.get("interested_companies") or [])
    if liked:
        parts.append("Companies they applied to on their own (they WANT to work there - "
                     "never lower a score for the company itself): " + ", ".join(liked[:15]) + ".")
    if not parts:
        return ""
    return (
        "\n\nCANDIDATE FEEDBACK HISTORY - learn from this. If THIS job resembles a "
        "company or role the candidate has rejected, or clearly matches one of their "
        "recurring rejection reasons, lower the scores accordingly:\n" + " ".join(parts)
    )


def compute_feedback_penalty(
    job: dict,
    signals: "dict | None",
    user_profile: "dict | None" = None,
) -> "tuple[int, str]":
    """Deterministic point penalty (0-60) for a job based on the user's pass history.

    This is the rule-based half of the hybrid learning loop. It is applied on top
    of the (already feedback-aware) match score and the final effective score is
    floored at 1 by callers, so a penalized job is demoted but never hidden.

    Returns (penalty, human_reason). penalty is subtracted from match_score for
    ranking; human_reason powers the "demoted" badge in the UI.
    """
    if not signals:
        return 0, ""
    company = (job.get("company") or "").strip().lower()
    title   = (job.get("title") or "").strip().lower()

    penalty = 0
    reason  = ""

    bad    = signals.get("bad_companies") or set()
    passed = signals.get("passed_companies") or {}

    interested = signals.get("interested_companies") or set()

    if company and company in bad:
        penalty += 45
        reason = "Company you flagged as bad"
    elif company and company in interested:
        pass        # he applied there himself: never "you've passed on this company"
    elif company:
        w = passed.get(company, 0)
        if w >= 1.5:
            penalty += 20
            reason = "You've passed on this company before"
        elif w >= 0.5:
            penalty += 8

    # Step 13: a stated salary at or below what he passed on as too low (or
    # below his Profile minimum), in the same currency. Unstated: no effect.
    floor = signals.get("salary_floor") or {}
    if floor:
        import salary as _salary
        s = _salary.parse(" ".join(str(job.get(k) or "") for k in ("title", "description")))
        if s and s["currency"] in floor and s["monthly_max"] <= floor[s["currency"]]:
            penalty += 15
            if not reason:
                reason = "Pays below your salary floor"

    # Similar-title penalty - exclude the user's own target-title tokens so we
    # never penalize the exact roles they're hunting for.
    disliked = set(signals.get("disliked_title_tokens") or set())
    if disliked and user_profile is not None:
        target_tokens = set()
        for ut in _parse_json_list(user_profile.get("job_titles")):
            for w in re.split(r"[^a-z0-9]+", (ut or "").lower()):
                if len(w) > 3:
                    target_tokens.add(w)
        disliked -= target_tokens
    if disliked and title:
        ttoks = {w for w in re.split(r"[^a-z0-9]+", title) if len(w) > 3}
        hits = len(ttoks & disliked)
        if hits:
            penalty += min(12, 6 * hits)
            if not reason:
                reason = "Similar to roles you've passed"

    # Location the user has repeatedly passed on.
    #
    # The comment here used to claim this "never fights their own preferred
    # city" - and nothing checked. The title branch above DOES guard against the
    # user's own targets (see `if disliked and user_profile is not None`); the
    # location branch never did. The match is a substring test, so once two
    # recent passes carried a "Wrong location" reason on jobs in, say,
    # "Tel Aviv, Israel", EVERY Tel Aviv job was demoted - including the ones in
    # the city the user actually asked for. The owner hit exactly this on staging
    # (2026-09-15): a 97% Tel Aviv role badged "Location you've passed on" while
    # Tel Aviv was his first preferred location.
    #
    # A pass on a job that IS in a preferred location is telling us something
    # about that job, not about the city.
    disliked_loc = signals.get("disliked_locations") or set()
    job_loc = (job.get("location") or "").strip().lower()
    preferred = [str(l).strip().lower() for l in
                 _parse_json_list((user_profile or {}).get("locations")) if str(l).strip()]
    in_preferred = any(pl in job_loc for pl in preferred) if job_loc else False
    if job_loc and disliked_loc and not in_preferred and \
            any(dl and dl in job_loc for dl in disliked_loc):
        penalty += 10
        if not reason:
            reason = "Location you've passed on"

    penalty = min(60, penalty)
    return penalty, (reason if penalty > 0 else "")


def _gemini_match_score(
    job_text: str,
    user_keywords: list,
    user_titles: list,
    seniority: str,
    job_title: str,
    api_key: str = "",
    feedback_notes: str = "",
    user_id: "int | None" = None,
) -> "tuple[int, int, int] | None":
    """Use Gemini 2.5 Flash for semantic job-fit scoring.

    Returns (kw_score_0_60, title_score_0_30, seniority_score_0_10)
    or None on any failure (caller falls back to keyword matching).
    Was _haiku_match_score; replaced with Gemini per Gemini-only refactor
    (2026-05-27).
    """
    key = api_key or os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GEMINI_KEY", "")
    if not key:
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
        "  seniority_score 0-10 seniority level fit"
        + (feedback_notes or "")
    )
    try:
        # Enforce strict JSON shape with responseSchema so we never have to
        # parse prose. Thinking disabled to avoid MAX_TOKENS truncation.
        schema = {
            "type": "OBJECT",
            "properties": {
                "keyword_score":   {"type": "INTEGER"},
                "title_score":     {"type": "INTEGER"},
                "seniority_score": {"type": "INTEGER"},
            },
            "required": ["keyword_score", "title_score", "seniority_score"],
        }
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 256,
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }).encode()
        # timeout stays short and retries stay off: this runs once per job in a
        # scoring loop, and the caller already degrades to the keyword heuristic.
        resp_data = gemini.generate(body, purpose="match_score", user_id=user_id,
                                    key=key, timeout=12, retries=0)
        candidates = resp_data.get("candidates") or []
        if not candidates:
            return None
        cand = candidates[0]
        content = cand.get("content") or {}
        parts = content.get("parts") or []
        text = (parts[0].get("text", "") if parts else "").strip()
        if not text:
            return None
        scores = json.loads(text)
        return (
            min(60, max(0, int(scores.get("keyword_score", 0)))),
            min(30, max(0, int(scores.get("title_score", 0)))),
            min(10, max(0, int(scores.get("seniority_score", 0)))),
        )
    except gemini.BudgetExceeded as be:
        # Deliberate degrade, not an error: the caller's keyword heuristic runs
        # instead. Named explicitly so narrowing the except below can never turn
        # a spent budget into a 500 for the whole jobs page.
        print("[match-score] %s - scoring with the keyword heuristic" % be)
        return None
    except Exception:
        return None


# Backward-compat alias — some callers still use the old name
_haiku_match_score = _gemini_match_score

def compute_match_score(job: dict, user_profile: dict, api_key: str = "",
                        signals: "dict | None" = None, user_id: "int | None" = None) -> int:
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

    # ── Try Gemini semantic scoring first (was Anthropic Haiku) ─────────────
    gemini_key = api_key or os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GEMINI_KEY", "")
    job_title_lower = (job.get("title") or "").lower()
    seniority = (user_profile.get("seniority") or "").lower()
    feedback_notes = _feedback_notes(signals)
    gemini_result = _gemini_match_score(job_text, user_keywords, user_titles, seniority,
                                        job_title_lower, gemini_key, feedback_notes,
                                        user_id=user_id)
    if gemini_result is not None:
        kw_score, title_score, seniority_score = gemini_result
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

def check_job_status(job_url: str, job_title: str, job_company: str, api_key: str,
                     user_id: "int | None" = None) -> dict:
    """
    Fetch the job URL and ask Claude (Haiku) whether the role is still open.

    Returns a dict:
        is_open    bool | None  — True=open, False=closed, None=unclear
        reason     str          — one-sentence explanation
        confidence str          — 'high' | 'medium' | 'low'
        status_check str        — 'open' | 'closed' | 'unknown'
    """
    if not api_key and not os.environ.get("GEMINI_API_KEY"):
        raise ValueError("GEMINI_API_KEY not configured.")
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

    # Gemini call (replaced Anthropic Haiku per Gemini-only refactor 2026-05-27)
    gemini_key = api_key or os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GEMINI_KEY", "")
    if not gemini_key:
        raise ValueError("GEMINI_API_KEY not configured.")
    schema = {
        "type": "OBJECT",
        "properties": {
            "is_open":    {"type": "BOOLEAN", "nullable": True},
            "reason":     {"type": "STRING"},
            "confidence": {"type": "STRING", "enum": ["high", "medium", "low"]},
        },
        "required": ["reason", "confidence"],
    }
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 512,
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }).encode()
    result = gemini.generate(body, purpose="check_job_status", user_id=user_id,
                             key=gemini_key, timeout=30)

    candidates = result.get("candidates") or []
    if not candidates:
        return {"is_open": None,
                "reason": "Gemini returned no candidates",
                "confidence": "low",
                "status_check": "unknown"}
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    raw = (parts[0].get("text", "") if parts else "").strip()
    if not raw:
        return {"is_open": None,
                "reason": "Gemini returned empty text",
                "confidence": "low",
                "status_check": "unknown"}
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1])

    data = json.loads(raw)
    is_open = data.get("is_open")
    data["status_check"] = "open" if is_open is True else ("closed" if is_open is False else "unknown")
    return data



_LETTER_PROMPT = """Write a cover letter from {name} for the job below. It should read as if {name}
wrote it: plain, warm, confident and specific - a senior professional writing to a hiring
manager they respect.

How it goes:
- Open with why this role makes sense: connect one concrete thing the job description asks for
  to one concrete thing {name} has done. No grand opening line, no boast.
- Then one or two short paragraphs: the two or three experiences from the CV that best match
  what the job needs most. For each, what {name} did and what changed, with the CV's own
  numbers, and in plain words why it matters for this job.
- Close briefly: what {name} would like to talk about. Sign off simply.

Accuracy (this matters most):
- Every fact about {name} - employers, titles, numbers, products, degrees - comes from the CV.
  Copy numbers exactly as the CV writes them. If the CV doesn't say it, don't say it.
- Facts about the company come only from the job description. No general praise of the company.

Voice:
- First person. Natural sentences of different lengths; contractions are fine.
- Never use: "I am writing to", "I am excited", "thrilled", "passionate", "dynamic",
  "results-driven", "proven track record", "synergy", "leverage", "spearheaded",
  "great fit", "perfect fit", "I believe I would". No em dashes.
- No headers, bullet points, subject line or placeholders like [Company].

Length: 180-250 words in 3-4 paragraphs. Begin with "Dear Hiring Team," unless the job
description names a person. End with "Best regards," and then {name} on its own line.

JOB: {title} at {company}
JOB DESCRIPTION:
{jd}

CV:
{cv}"""


def _letter_text(prompt: str, gemini_key: str, user_id) -> str:
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.6, "maxOutputTokens": 1500,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }).encode("utf-8")
    try:
        gdata = gemini.generate(body, purpose="cover_letter", user_id=user_id, key=gemini_key, timeout=60)
    except urllib.error.HTTPError as e:
        raise RuntimeError("Gemini HTTP " + str(e.code) + ": " + e.read().decode("utf-8", errors="replace")[:400])
    except Exception as e:
        raise RuntimeError("Gemini exception: " + str(e))
    cands = gdata.get("candidates", [])
    if not cands or "content" not in cands[0]:
        raise RuntimeError("Gemini returned no letter: " + json.dumps(gdata)[:300])
    return cands[0]["content"]["parts"][0]["text"].strip()


def write_cover_letter(job: dict, profile: dict, api_key: str = "", user_id=None,
                       cv_text: str = "", name: str = "", llm=None) -> dict:
    """{letter, notes}. Written from the full CV (cv_text); it used to get only
    the 2-3 sentence profile summary, so a prompt demanding "specific,
    measurable experience" had none to use (The owner, 2026-11-08: "not good
    enough"). A number that is in neither the CV nor the job description gets
    one rewrite with the problem named; if it survives, that sentence is
    removed and noted."""
    import tailor
    gemini_key = api_key or os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GEMINI_KEY", "")
    cv = (cv_text or profile.get("cv_summary") or "").strip()[:9000]
    if not cv:
        raise RuntimeError("No CV to write from - upload your CV first.")
    jd = (job.get("full_description") or job.get("description") or job.get("title", ""))[:5000]
    name = (name or "").strip() or "the candidate"
    prompt = _LETTER_PROMPT.format(name=name, title=job.get("title", "the role"),
                                   company=job.get("company", "the company"), jd=jd, cv=cv)
    if llm is None:
        if not gemini_key:
            raise RuntimeError("GEMINI_API_KEY is not set in Railway environment variables.")
        llm = lambda p: _letter_text(p, gemini_key, user_id)
    allowed = tailor._numbers(cv) | tailor._numbers(jd)
    letter = llm(prompt)
    bad = sorted(tailor._numbers(letter) - allowed)
    if bad:
        letter = llm(prompt + "\n\nYour previous draft used numbers that are in neither the CV nor the job "
                     "description: " + ", ".join(bad) + ". Write it again without them; keep the rest.")
        bad = sorted(tailor._numbers(letter) - allowed)
    notes = []
    if bad:
        kept = []
        for para in letter.split("\n"):
            sents = re.split(r"(?<=[.!?])\s+", para)
            ok = [x for x in sents if not (tailor._numbers(x) - allowed)]
            notes += ["Removed: \"%s\" (a number not in your CV)" % x.strip() for x in sents if tailor._numbers(x) - allowed]
            kept.append(" ".join(ok))
        letter = "\n".join(kept)
    print("[cover-letter] written (" + str(len(letter)) + " chars, " + str(len(notes)) + " removed)")
    return {"letter": letter.strip(), "notes": notes}


def generate_cover_letter(job: dict, profile: dict, api_key: str = "",
                          user_id: "int | None" = None, cv_text: str = "", name: str = "") -> str:
    """The letter only (older callers). See write_cover_letter."""
    return write_cover_letter(job, profile, api_key, user_id, cv_text=cv_text, name=name)["letter"]


