"""
ai_analysis.py — CV analysis via Claude API for Job Hunter
"""
import base64
import json
import os
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
        "model": "claude-opus-4-6",
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
