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



_LETTER_PROMPT = """You are {name}, writing a short cover letter for the job below, yourself, the way
a senior person writes to someone they would like to work with. Not a summary of the CV
mapped onto the job description: a letter from a person.

What you know about yourself (your own positioning, then your CV):
{about}

How it goes (three short paragraphs, 150-200 words in all):
1. Start from you, not from the job. What you have been working on and what pulls you toward
   this particular job - something specific in the job description that you actually find
   interesting, said the way you would say it to a person. Never open with the job title or
   the company name, never restate what the job is.
2. One real story from the CV that is the closest thing to what they need: what the situation
   was, what you did, what changed (the CV's own numbers, copied exactly). One story told
   well beats three listed.
3. One or two sentences: what you would like to talk about with them. Then "Best regards,"
   and {name} on its own line.

Sound like a person:
- First person, plain words, contractions, sentences of different lengths. Something a
  colleague could hear you say out loud.
- Never these, or anything like them: "aligns with", "aligns well", "directly relevant",
  "this experience", "my experience in", "my background", "I am confident", "I believe",
  "I am writing to", "I'm interested in discussing", "excited", "thrilled", "passionate",
  "leverage", "spearheaded", "drove", "empowering", "democratizing", "operationalized",
  "proven track record", "results-driven", "dynamic", "synergy", "great fit", "perfect fit",
  "which prepared me", "this involved", "has consistently". No em dashes.
- Never describe yourself in the third person or with adjectives about yourself ("seasoned",
  "strategic leader"). Show it with what you did.

Accuracy (this matters most):
- Every fact about you - employers, titles, numbers, products - comes from the CV. If the CV
  doesn't say it, don't say it. Don't invent feelings about your past either; the "why" comes
  from what you have done and what the job describes.
- Facts about the company come only from the job description. No praise of the company.
- No headers, bullets, subject line or placeholders like [Company]. Begin with "Dear Hiring
  Team," unless the job description names a person.

JOB: {title} at {company}
JOB DESCRIPTION:
{jd}

CV:
{cv}"""


_NOTE_PROMPT = """You are {name}. The application form for the job below has a short "personal note"
box. Write what you would type there yourself: 2-3 sentences, at most 450 characters, to the
person who will read the application.

- Open with the job, not with yourself: the one specific thing in this job description that
  made you stop and want to talk to them, in your own words (not the posting's).
- Then one concrete thing you did that is closest to it - the company, what you built or
  changed, the CV's own number copied exactly.
- Never describe yourself with a title or label ("I'm an Executive Product Leader",
  "I'm an AI product executive", "I lead AI product transformation", "a seasoned..."):
  the reader has your CV. Never "I'm drawn to", "caught my eye", "really grabs me",
  "stands out", "could be valuable here", "seems like a good match".
- Plain and a bit informal, like a message to a person. Contractions are fine. No em
  dashes, no "+22%" style - write "22%". No greeting line, no sign-off, no name.
- Never: "aligns", "relevant", "this experience", "my background", "excited",
  "passionate", "leverage", "boosted", "I believe", "I am confident", "great fit".
- Every fact about you comes from the CV; facts about the company only from the job.

JOB: {title} at {company}
JOB DESCRIPTION:
{jd}

CV:
{cv}"""

# Phrases that make a letter read as machine-written (the owner, 2026-11-23, on the Commit
# letter: "this is not written by human"). A draft using one gets one rewrite with them named.
_ROBOT_PHRASES = [
    r"\baligns?\b", r"\baligned\b", r"directly relevant", r"\bthis (direct )?experience\b",
    r"\bmy background\b", r"\bmy experience (in|with)\b", r"i(?:'| a)m interested in discussing",
    r"\bi am confident\b", r"\bi believe\b", r"\bpassionate\b", r"\bexcited\b", r"\bthrilled\b",
    r"\bleverag", r"\bspearhead", r"\bempower", r"\bdemocratiz", r"\boperationaliz",
    r"proven track record", r"results-driven", r"\bsynerg", r"(great|perfect) fit",
    r"which prepared me", r"\bthis involved\b", r"has consistently", r"have consistently",
    r"^\s*i(?:'| a)m an? [a-z ]{0,30}(leader|executive|manager|director|expert|professional)\b",
    r"\bi lead ai product\b", r"\bi(?:'| a)m drawn to\b", r"caught my eye", r"really grabs me",
    r"\bstands out\b", r"could be valuable here", r"seems like a good match", r"\bboost(ed|ing)?\b", r"\+\d+%",
    r"\bdrove\b", r"\bdriving\b", r"is what i(?:'| a)m looking for", r"\bi(?:'| a)m looking for\b",
    r"—",
]


def robot_phrases(text: str) -> list:
    """The machine-sounding phrases in text (see _ROBOT_PHRASES), as written."""
    found = []
    for pat in _ROBOT_PHRASES:
        m = re.search(pat, text or "", re.I)
        if m:
            found.append(m.group(0))
    return found


def _opens_with_the_job(text: str, job: dict) -> bool:
    """Does the first sentence restate the job ("The X role at Y...", "The CRM Head of
    Product role, with its ... mission")? 0.7.1 compared the exact title text and
    missed a reworded title (Fullpath, 2026-11-23)."""
    body = re.sub(r"^\s*(dear|hi|hello)[^\n]*\n+", "", text or "", flags=re.I).strip().lower()
    first = re.split(r"(?<=[.!?])\s", body, 1)[0][:220]
    if re.match(r"^(the|this|your)\b[^.]{0,80}\b(role|position|opening|opportunity|job)\b", first):
        return True
    words = {w for w in re.findall(r"[a-z]+", (job.get("title") or "").lower()) if len(w) > 2 and w not in ("and", "the", "for")}
    hits = sum(1 for w in words if re.search(r"\b%s\b" % re.escape(w), first[:90]))
    return bool(words) and hits >= min(2, len(words))


def _quotes_the_posting(text: str, jd: str) -> str:
    """A phrase in quotes copied from the job description ("category-defining product")."""
    for q in re.findall(r'["\u201c]([^"\u201d]{6,80})["\u201d]', text or ""):
        if q.lower() in (jd or "").lower():
            return q
    return ""

_STOP = {"the", "a", "an", "and", "of", "to", "in", "for", "with", "on", "at", "by", "i", "my", "is", "it", "that", "as"}


def _copied_from(text: str, source: str, n: int = 4) -> str:
    """The first run of n+ words text shares with source (the CV summary), or ''.
    The owner, 2026-11-23: a note opening "I'm an Executive Product Leader,
    transforming platforms with AI" was his summary restated."""
    words = re.findall(r"[a-z0-9']+", (source or "").lower())
    grams = {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}
    tw = re.findall(r"[a-z0-9']+", (text or "").lower())
    for i in range(len(tw) - n + 1):
        g = " ".join(tw[i:i + n])
        if g in grams and sum(w not in _STOP for w in g.split()) >= 2:
            return g
    return ""


def _problems(text: str, job: dict, summary: str = "") -> list:
    problems = robot_phrases(text)
    copied = _copied_from(text, summary)
    if copied:
        problems.append('it repeats the CV summary ("%s ...") - say it your own way' % copied)
    if _opens_with_the_job(text, job):
        problems.append("it opens by restating the job title / role - start with what you think about the work")
    quoted = _quotes_the_posting(text, job.get("full_description") or job.get("description") or "")
    if quoted:
        problems.append('it quotes the posting ("%s") - use your own words' % quoted)
    return problems


def _humanize(llm, prompt: str, text: str, job: dict, summary: str = "") -> str:
    """Up to two rewrites while the draft sounds machine-written, the problems named
    each time; the draft with the fewest problems is kept. (0.7.1 rewrote once, and
    only for problems its checks caught - Fullpath's note passed them all, 2026-11-23.)"""
    best, best_p = text, _problems(text, job, summary)
    for _ in range(2):
        if not best_p:
            break
        again = llm(prompt + "\n\nYour previous draft:\n" + best + "\n\nIt sounded machine-written: " + "; ".join(
            '"%s"' % p if not p.startswith("it ") else p for p in best_p) +
            ". Write it again as a person would, without those; keep the facts.")
        again_p = _problems(again, job, summary)
        if len(again_p) <= len(best_p):
            best, best_p = again, again_p
    return best

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
    about = (profile.get("about_me") or profile.get("cv_summary") or "").strip()[:1200] or "(see the CV)"
    prompt = _LETTER_PROMPT.format(name=name, title=job.get("title", "the role"), about=about,
                                   company=job.get("company", "the company"), jd=jd, cv=cv)
    if llm is None:
        if not gemini_key:
            raise RuntimeError("GEMINI_API_KEY is not set in Railway environment variables.")
        llm = lambda p: _letter_text(p, gemini_key, user_id)
    allowed = tailor._numbers(cv) | tailor._numbers(jd)
    letter = _humanize(llm, prompt, llm(prompt), job, profile.get("cv_summary") or "")
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


_NOTE_PARTS_PROMPT = """You are {name}, applying for the job below. Write two sentences for the
application form's "personal note" box, in your own first-person voice, as JSON only:

{{"hook": "...", "proof": "..."}}

hook - ONE sentence about a specific problem or piece of work in this job description that you
  would genuinely want to work on, and why it interests you. Talk about the work itself.
  It must NOT contain: the job title or any part of it ({title}), the company name ({company}),
  the words role / position / job / opportunity / mission, a quote from the posting, or any
  label for yourself ("I'm a product leader..."). Not "I'm drawn to", "exactly what I enjoy",
  "is what I'm looking for", "excited", "passionate".
proof - ONE sentence: the closest thing you actually did, from the CV - the company, what you
  built or changed, and the CV's own number copied exactly ("22%", not "+22%"). No "drove",
  "boosted", "spearheaded", "leveraged".

Plain, specific, a little informal. No em dashes.

JOB: {title} at {company}
JOB DESCRIPTION:
{jd}

CV:
{cv}"""

_ROLE_WORDS = re.compile(r"\b(role|position|job|opportunity|opening|mission)\b", re.I)


def _note_parts(raw: str) -> dict:
    """{'hook', 'proof'} from the model's reply (JSON, possibly fenced)."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict):
                return {"hook": str(d.get("hook") or "").strip(), "proof": str(d.get("proof") or "").strip()}
        except (TypeError, ValueError):
            pass
    parts = re.split(r"(?<=[.!?])\s+", (raw or "").strip(), 1)
    return {"hook": parts[0].strip(), "proof": parts[1].strip() if len(parts) > 1 else ""}


def _hook_problems(hook: str, job: dict) -> list:
    """What disqualifies the hook sentence: naming the job, the company, a role word."""
    probs = []
    low = (hook or "").lower()
    company = (job.get("company") or "").strip().lower()
    if not hook:
        probs.append("the hook is empty")
    if _ROLE_WORDS.search(hook or ""):
        probs.append('the hook uses "%s"' % _ROLE_WORDS.search(hook).group(0))
    if company and len(company) > 2 and company in low:
        probs.append("the hook names the company")
    words = {w for w in re.findall(r"[a-z]+", (job.get("title") or "").lower()) if len(w) > 2 and w not in ("and", "the", "for")}
    if words and sum(1 for w in words if re.search(r"\b%s\b" % re.escape(w), low)) >= min(2, len(words)):
        probs.append("the hook repeats the job title")
    # Copying the posting is not personal: Fullpath's hooks on production repeated
    # "technically complex, data-intensive product ..." word for word (2026-11-23).
    copied = _copied_from(hook, job.get("full_description") or job.get("description") or "", n=4)
    if copied:
        probs.append('the hook copies the posting ("%s ...") - say it in your own words' % copied)
    return probs


def write_note(job: dict, profile: dict, api_key: str = "", user_id=None,
               cv_text: str = "", name: str = "", llm=None) -> str:
    """The short "personal note" for a form's note box (Comeet "Personal note",
    Lever "Additional information"): a sentence about the work + a sentence of proof.
    Built from two separately checked parts: asked for as one paragraph, Gemini opened
    with "The CRM Head of Product role..." on every rewrite (production, 2026-11-23),
    whatever the prompt said."""
    import tailor
    gemini_key = api_key or os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GEMINI_KEY", "")
    cv = (cv_text or profile.get("cv_summary") or "").strip()[:9000]
    if not cv:
        raise RuntimeError("No CV to write from - upload your CV first.")
    jd = (job.get("full_description") or job.get("description") or job.get("title", ""))[:5000]
    name = (name or "").strip() or "the candidate"
    prompt = _NOTE_PARTS_PROMPT.format(name=name, title=job.get("title", "the role"),
                                       company=job.get("company", "the company"), jd=jd, cv=cv)
    if llm is None:
        if not gemini_key:
            raise RuntimeError("GEMINI_API_KEY is not set in Railway environment variables.")
        llm = lambda p: _letter_text(p, gemini_key, user_id)
    summary = profile.get("cv_summary") or ""
    best, best_n = None, 99
    ask = prompt
    for _ in range(3):
        parts = _note_parts(llm(ask))
        probs = _hook_problems(parts["hook"], job) + _problems(parts["hook"] + " " + parts["proof"], job, summary)
        probs = [x for x in probs if not x.startswith("it opens by restating")]   # the hook check covers this
        if len(probs) < best_n:
            best, best_n = parts, len(probs)
        if not probs:
            break
        ask = prompt + "\n\nYour previous answer: " + json.dumps(parts) + "\nProblems: " + "; ".join(probs) + \
            ". Write both sentences again without those problems."
    first = lambda t: re.split(r"(?<=[.!?])\s+", (t or "").strip(), 1)[0]   # one sentence each
    hook, proof = first(best["hook"]), first(best["proof"])
    # Nothing useful to say -> no note (the owner, 2026-11-23: "if you can't write
    # anything useful - keep it empty"). Useful = a clean hook AND a clean proof.
    if _hook_problems(hook, job) or _problems(hook + " " + proof, job, summary) or not proof:
        return ""
    note = (hook + " " + proof).strip()
    allowed = tailor._numbers(cv) | tailor._numbers(jd)
    if tailor._numbers(note) - allowed:      # a number not in the CV or the job: drop that sentence
        note = " ".join(x for x in re.split(r"(?<=[.!?])\s+", note) if not (tailor._numbers(x) - allowed))
    note = note.strip().strip('"')
    # Never cut mid-word (production: "...engagem"): drop whole sentences past 450.
    while len(note) > 450 and re.search(r"[.!?]\s+\S", note):
        note = re.split(r"(?<=[.!?])\s+(?=\S[^.!?]*[.!?]?\s*$)", note)[0]
    return note if len(note) <= 450 else note[:450].rsplit(" ", 1)[0].rstrip(",;:") + "."


def generate_cover_letter(job: dict, profile: dict, api_key: str = "",
                          user_id: "int | None" = None, cv_text: str = "", name: str = "") -> str:
    """The letter only (older callers). See write_cover_letter."""
    return write_cover_letter(job, profile, api_key, user_id, cv_text=cv_text, name=name)["letter"]


