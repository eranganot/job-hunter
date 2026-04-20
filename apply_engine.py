"""apply_engine.py — Playwright-based job application submission for Job Hunter.
Uses headless Chromium + Claude to:
1. Navigate to the job application URL
2. Detect form structure and ATS type
3. Fill fields using applicant data extracted from CV
4. Submit the form
5. Verify confirmation on the result page
Statuses: 'confirmed' | 'submitted' | 'failed' | 'manual_required'
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime

# ── Playwright ────────────────────────────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# ── ATS detection ─────────────────────────────────────────────────────────────

def _is_greenhouse(url: str) -> bool:
    """Check if URL is a Greenhouse job board."""
    return bool(re.search(r'boards\.greenhouse\.io|job-boards\.greenhouse\.io|grnh\.se', url))

def _is_lever(url: str) -> bool:
    """Check if URL is a Lever job board."""
    return bool(re.search(r'jobs\.lever\.co|lever\.co/.*?/[a-f0-9-]+', url))

# Job board domains — URLs from these domains get resolved to the company's own career page
_JOB_BOARD_DOMAINS = {
    'linkedin.com', 'indeed.com', 'glassdoor.com', 'ziprecruiter.com',
    'monster.com', 'careerjet.com', 'simplyhired.com', 'dice.com',
    'angel.co', 'wellfound.com', 'otta.com', 'remoteok.com',
    'weworkremotely.com', 'remotive.io', 'geektime.co.il', 'alljobs.co.il',
    'jobmaster.co.il', 'drushim.co.il', 'jobnet.co.il', 'gotfriends.co.il',
}

def _is_job_board(url: str) -> bool:
    """Return True if the URL is from a job aggregator rather than a company's own site."""
    try:
        host = urllib.parse.urlparse(url).netloc.lower().lstrip('www.')
        return any(host == d or host.endswith('.' + d) for d in _JOB_BOARD_DOMAINS)
    except Exception:
        return False

# ── Company career page resolver ──────────────────────────────────────────────

def _find_company_apply_url(company: str, job_title: str) -> str | None:
    """Search for the company's direct job listing URL (bypassing job boards).

    Strategy:
      1. Google-search for the role on the company's own ATS
      2. Prefer Greenhouse / Lever / Workday / Ashby links
      3. Fall back to any career-looking URL that isn't a job board
    """
    if not PLAYWRIGHT_AVAILABLE:
        return None
    # Build a targeted search query
    query = (
        f'"{company}" "{job_title}" apply '
        f'site:greenhouse.io OR site:lever.co OR site:workday.com '
        f'OR site:ashbyhq.com OR site:bamboohr.com OR site:smartrecruiters.com'
    )
    search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}&num=8&hl=en"

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"],
            )
            ctx = browser.new_context(user_agent=_UA)
            page = ctx.new_page()
            page.set_default_timeout(15_000)

            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=20_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=5_000)
                except PWTimeout:
                    pass
            except Exception as e:
                print(f"[apply-engine] career search navigate error: {e}")
                browser.close()
                return None

            # Extract all result links, excluding Google's own pages
            links: list[str] = page.evaluate("""
                () => Array.from(document.querySelectorAll('a[href]'))
                    .map(a => a.href)
                    .filter(h => h.startsWith('http') && !h.includes('google.com')
                              && !h.includes('youtube.com') && !h.includes('wikipedia.org'))
                    .slice(0, 20)
            """)
            browser.close()

            # Tier 1: known ATS domains — highest confidence
            _ATS_DOMAINS = [
                'greenhouse.io', 'lever.co', 'workday.com',
                'ashbyhq.com', 'bamboohr.com', 'smartrecruiters.com',
                'icims.com', 'taleo.net', 'successfactors.com', 'jobvite.com',
            ]
            for link in links:
                if any(d in link for d in _ATS_DOMAINS):
                    print(f"[apply-engine] Found ATS URL: {link}")
                    return link

            # Tier 2: any careers/jobs URL that isn't a job board
            for link in links:
                if any(kw in link.lower() for kw in ['career', 'job', 'position', 'apply', '/work']):
                    if not _is_job_board(link):
                        print(f"[apply-engine] Found career URL: {link}")
                        return link

            print(f"[apply-engine] No direct career URL found for {company} / {job_title}")
            return None

    except Exception as e:
        print(f"[apply-engine] _find_company_apply_url error: {e}")
        return None

# ── ATS-specific form handlers ────────────────────────────────────────────────

def _gh_fill(page, selector: str, value: str):
    """Safely fill a form field, skipping if not found or empty value."""
    if not value:
        return
    try:
        el = page.query_selector(selector)
        if el and el.is_visible():
            el.fill(value)
    except Exception:
        pass

def _apply_greenhouse(page, applicant: dict, cv_path: str | None) -> dict:
    """Handle Greenhouse application forms with known structure."""
    result = {"success": False, "status": "failed", "confirmation_text": "", "error": ""}
    try:
        # Click "Apply for this job" button if present
        for sel in ['a:has-text("Apply")', 'button:has-text("Apply")', 'a[href*="application"]']:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    page.wait_for_load_state("networkidle", timeout=8_000)
                    break
            except Exception:
                continue

        # Fill standard Greenhouse fields
        _gh_fill(page, '#first_name', applicant.get('first_name', ''))
        _gh_fill(page, '#last_name', applicant.get('last_name', ''))
        _gh_fill(page, '#email', applicant.get('email', ''))
        _gh_fill(page, '#phone', applicant.get('phone', ''))
        _gh_fill(page, 'input[name*="linkedin"], input[autocomplete*="url"]', applicant.get('linkedin_url', ''))
        _gh_fill(page, 'input[name*="location"], input[name*="city"]', applicant.get('location', ''))

        # Upload CV
        if cv_path:
            for sel in ['input[type="file"]', '#resume', 'input[name*="resume"]', 'input[name*="cv"]']:
                try:
                    page.set_input_files(sel, cv_path)
                    print(f"[apply-engine] GH: uploaded CV via {sel}")
                    break
                except Exception:
                    continue

        # Submit
        submitted = False
        for sel in ['button[type="submit"]', 'input[type="submit"]',
                    'button:has-text("Submit")', 'button:has-text("Apply")']:
            try:
                page.click(sel, timeout=5_000)
                submitted = True
                break
            except Exception:
                continue

        if not submitted:
            result["error"] = "Greenhouse: could not find submit button"
            return result

        page.wait_for_load_state("networkidle", timeout=10_000)
        confirm_text = page.evaluate("document.body.innerText") or ""
        if any(w in confirm_text.lower() for w in ["thank", "received", "submitted", "confirmation"]):
            result.update(success=True, status="confirmed", confirmation_text=confirm_text[:500])
        else:
            result.update(success=True, status="submitted", confirmation_text=confirm_text[:500])
        return result
    except Exception as e:
        result["error"] = f"Greenhouse handler error: {e}"
        return result

def _apply_lever(page, applicant: dict, cv_path: str | None) -> dict:
    """Handle Lever application forms with known structure."""
    result = {"success": False, "status": "failed", "confirmation_text": "", "error": ""}
    try:
        # Click "Apply for this job" link
        for sel in ['a.postings-btn', 'a:has-text("Apply")', '.posting-btn-submit']:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    page.wait_for_load_state("networkidle", timeout=8_000)
                    break
            except Exception:
                continue

        # Fill standard Lever fields
        _gh_fill(page, 'input[name="name"]',
                 f"{applicant.get('first_name', '')} {applicant.get('last_name', '')}".strip())
        _gh_fill(page, 'input[name="email"]', applicant.get('email', ''))
        _gh_fill(page, 'input[name="phone"]', applicant.get('phone', ''))
        _gh_fill(page, 'input[name="org"], input[name*="company"]', applicant.get('current_company', ''))
        _gh_fill(page, 'input[name*="linkedin"], input[name*="urls[LinkedIn]"]', applicant.get('linkedin_url', ''))

        # Upload CV
        if cv_path:
            for sel in ['input[type="file"]', 'input[name="resume"]', 'input[name*="cv"]']:
                try:
                    page.set_input_files(sel, cv_path)
                    print(f"[apply-engine] Lever: uploaded CV via {sel}")
                    break
                except Exception:
                    continue

        # Submit
        submitted = False
        for sel in ['button[type="submit"]', 'button:has-text("Submit")',
                    'a:has-text("Submit")', '.postings-btn']:
            try:
                page.click(sel, timeout=5_000)
                submitted = True
                break
            except Exception:
                continue

        if not submitted:
            result["error"] = "Lever: could not find submit button"
            return result

        page.wait_for_load_state("networkidle", timeout=10_000)
        confirm_text = page.evaluate("document.body.innerText") or ""
        if any(w in confirm_text.lower() for w in ["thank", "received", "submitted", "application"]):
            result.update(success=True, status="confirmed", confirmation_text=confirm_text[:500])
        else:
            result.update(success=True, status="submitted", confirmation_text=confirm_text[:500])
        return result
    except Exception as e:
        result["error"] = f"Lever handler error: {e}"
        return result

# ── Failure classifier ────────────────────────────────────────────────────────

def _classify_failure(error: str, page_text: str = "") -> tuple[str, str]:
    """Return (failure_type, failure_detail).
    failure_type: captcha | timeout | login_wall | form_validation | network_error | other
    """
    combined = ((error or "") + " " + (page_text or "")).lower()
    detail = (error or "")[:300]
    if any(k in combined for k in ("captcha", "recaptcha", "hcaptcha", "cloudflare",
                                    "verify you are human", "bot detection", "i am not a robot")):
        return "captcha", detail
    if any(k in combined for k in ("timeout", "timed out", "time out", "deadline exceeded")):
        return "timeout", detail
    if any(k in combined for k in ("login", "sign in", "log in", "please log",
                                    "create an account", "login required", "account required")):
        return "login_wall", detail
    if any(k in combined for k in ("required field", "invalid email", "validation error",
                                    "please fill", "missing required", "field is required")):
        return "form_validation", detail
    if any(k in combined for k in ("connection", "network error", "refused",
                                    "unreachable", "name resolution", "ssl error", "certificate")):
        return "network_error", detail
    return "other", detail

def _add_failure_type(res: dict) -> dict:
    """Ensure apply_failure_type/detail are set on a result dict."""
    if "apply_failure_type" not in res:
        if not res.get("success", False) and res.get("error"):
            ft, fd = _classify_failure(res["error"])
            res["apply_failure_type"] = ft
            res["apply_failure_detail"] = fd
        else:
            res["apply_failure_type"] = None
            res["apply_failure_detail"] = None
    return res

# ── Config ────────────────────────────────────────────────────────────────────

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_BLOCKER_PHRASES = [
    "sign in to apply", "log in to apply", "login to apply",
    "create an account", "register to apply", "please log in",
    "sign up to apply", "you must be logged in",
]

_CONFIRMATION_PHRASES = [
    "application submitted", "application received",
    "thank you for applying", "thanks for applying",
    "successfully submitted", "application complete",
    "we received your application", "your application has been",
    "we'll be in touch", "we will be in touch",
]

# ── Claude helpers ────────────────────────────────────────────────────────────

def _claude(prompt: str, max_tokens: int = 1024) -> str:
    """Call Claude and return the text response."""
    payload = json.dumps({
        "model": "claude-haiku-4-5",  # fixed: was claude-haiku-4-5-20251001
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        result = json.loads(resp.read())
    text = result["content"][0]["text"].strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    return text.strip()

def _claude_json(prompt: str, max_tokens: int = 1024):
    """Call Claude and parse the JSON response."""
    text = _claude(prompt, max_tokens)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            return json.loads(m.group())
        m = re.search(r'\[.*\]', text, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise ValueError(f"No JSON found: {text[:200]}")

# ── Applicant data extraction ─────────────────────────────────────────────────

def extract_applicant_data(cv_text: str, email: str) -> dict:
    """Extract structured applicant data from CV text using Claude."""
    empty = {
        "email": email, "full_name": "", "first_name": "", "last_name": "",
        "phone": "", "linkedin_url": "", "location": "", "current_title": "",
        "current_company": "", "years_experience": 0, "summary": "",
    }
    if not cv_text or not ANTHROPIC_KEY:
        return empty
    try:
        result = _claude_json(
            f'Extract the applicant info from this CV. Return ONLY valid JSON:\n'
            f'{{"full_name":"First Last","first_name":"First","last_name":"Last",'
            f'"email":"{email}","phone":"phone or empty","linkedin_url":"LinkedIn URL or empty",'
            f'"location":"City, Country","current_title":"latest job title",'
            f'"current_company":"latest employer","years_experience":0,'
            f'"summary":"2-3 sentence professional summary"}}\n\nCV:\n{cv_text[:4000]}',
            max_tokens=512,
        )
        result["email"] = email
        return result
    except Exception as e:
        print(f"[apply-engine] extract_applicant_data error: {e}")
        return empty

# ── URL alive check ───────────────────────────────────────────────────────────

def check_url_alive(url: str, timeout: int = 8) -> bool:
    """Return True if the URL responds with a non-error HTTP status."""
    if not url:
        return False
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status < 400
        except urllib.error.HTTPError as e:
            return e.code < 400
        except Exception:
            if method == "HEAD":
                continue
            return False
    return False

# ── Core application submission ───────────────────────────────────────────────

def submit_application(
    job_url: str,
    job_title: str,
    company: str,
    applicant: dict,
    cv_path: str | None,
    api_key: str = "",
) -> dict:
    """
    Submit a job application using headless Chromium + Claude.

    If job_url is from a job board (LinkedIn, Indeed, etc.), automatically
    searches for and navigates to the company's own career page instead.

    Returns:
        {
            "success": bool,
            "status": "confirmed" | "submitted" | "failed" | "manual_required",
            "confirmation_text": str,
            "error": str,
            "resolved_url": str,  # actual URL used (may differ from job_url)
        }
    """
    global ANTHROPIC_KEY
    if api_key:
        ANTHROPIC_KEY = api_key

    _base = {
        "success": False, "status": "failed", "confirmation_text": "", "error": "",
        "apply_failure_type": None, "apply_failure_detail": None, "resolved_url": job_url,
    }

    if not PLAYWRIGHT_AVAILABLE:
        return {**_base, "status": "manual_required",
                "error": "Playwright not installed — run: playwright install chromium --with-deps"}
    if not job_url:
        return {**_base, "error": "No job URL provided"}

    # ── Resolve job board URLs to company's own career page ──────────────────
    actual_url = job_url
    if _is_job_board(job_url):
        print(f"[apply-engine] Job board URL detected ({job_url}), searching for company career page…")
        direct = _find_company_apply_url(company, job_title)
        if direct:
            print(f"[apply-engine] Resolved to: {direct}")
            actual_url = direct
        else:
            print(f"[apply-engine] Could not find direct URL — keeping original: {job_url}")
    _base["resolved_url"] = actual_url

    try:
        with sync_playwright() as pw:
            print(f"[apply-engine] Launching Chromium…")
romium…")
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"],
            )
            ctx = browser.new_context(user_agent=_UA)
            page = ctx.new_page()
            page.set_default_timeout(20_000)

            # 1. Navigate ─────────────────────────────────────────────────────
            try:
                page.goto(actual_url, wait_until="domcontentloaded", timeout=30_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except PWTimeout:
                    pass
            except PWTimeout:
                browser.close()
                return {**_base, "apply_failure_type": "timeout",
                        "apply_failure_detail": "Page load timed out after 30s",
                        "error": "Page load timed out"}

            page_text = page.evaluate("document.body.innerText") or ""
            lower = page_text.lower()

            # 2. Check for login walls ─────────────────────────────────────────
            if any(p in lower for p in _BLOCKER_PHRASES):
                browser.close()
                return {**_base, "status": "manual_required",
                        "error": "Login or account creation required to apply"}

            # 2b. ATS-specific fast path ──────────────────────────────────────
            if _is_greenhouse(actual_url):
                print(f"[apply-engine] Detected Greenhouse ATS")
                res = _apply_greenhouse(page, applicant, cv_path)
                browser.close()
                res["resolved_url"] = actual_url
                return _add_failure_type(res)

            if _is_lever(actual_url):
                print(f"[apply-engine] Detected Lever ATS")
                res = _apply_lever(page, applicant, cv_path)
                browser.close()
                res["resolved_url"] = actual_url
                return _add_failure_type(res)

            # 3. Click Apply button if no form visible yet ─────────────────────
            page_html = page.content()
            if not page.query_selector("form"):
                try:
                    nav = _claude_json(
                        f'Job page for "{job_title}" at "{company}".\n'
                        f'Text: {page_text[:1500]}\n'
                        f'Is there an Apply button? Return JSON: '
                        f'{{"has_apply_button":true/false,"button_text":"exact text or empty"}}',
                        max_tokens=150,
                    )
                    if nav.get("has_apply_button") and nav.get("button_text"):
                        btn = page.get_by_text(nav["button_text"], exact=False).first
                        if btn:
                            btn.click()
                            try:
                                page.wait_for_load_state("networkidle", timeout=8_000)
                            except PWTimeout:
                                pass
                            page_text = page.evaluate("document.body.innerText") or ""
                            page_html = page.content()
                            lower = page_text.lower()
                            if any(p in lower for p in _BLOCKER_PHRASES):
                                browser.close()
                                return {**_base, "status": "manual_required",
                                        "apply_failure_type": "login_wall",
                                        "apply_failure_detail": "Login required after clicking Apply",
                                        "error": "Login required after clicking Apply"}
                except Exception as e:
                    print(f"[apply-engine] apply-button error: {e}")

            # 4. Ask Claude for form-filling instructions ──────────────────────
            try:
                instructions = _claude_json(
                    f'Fill job application for "{job_title}" at "{company}".\n\n'
                    f'Applicant data:\n{json.dumps(applicant, indent=2)}\n\n'
                    f'Page HTML (first 8000 chars):\n{page_html[:8000]}\n\n'
                    f'Return a JSON ARRAY of interactions (no explanation):\n'
                    f' {{"action":"fill","selector":"CSS","value":"text"}}\n'
                    f' {{"action":"select","selector":"CSS","value":"option value or label"}}\n'
                    f' {{"action":"upload","selector":"CSS","file":"cv"}}\n'
                    f' {{"action":"check","selector":"CSS"}}\n'
                    f' {{"action":"click","selector":"CSS"}}\n'
                    f' {{"action":"submit","selector":"CSS of submit button"}}\n\n'
                    f'Rules: only include visible/required fields; end with submit.\n'
                    f'If impossible (login/captcha/no form), return {{"error":"reason"}}',
                    max_tokens=2048,
                )
            except Exception as e:
                browser.close()
                return {**_base, "status": "manual_required",
                        "error": f"Form analysis failed: {e}"}

            if isinstance(instructions, dict) and "error" in instructions:
                browser.close()
                return {**_base, "status": "manual_required", "error": instructions["error"]}

            if not isinstance(instructions, list) or not instructions:
                browser.close()
                return {**_base, "status": "manual_required",
                        "error": "No form interactions generated"}

            # 5. Execute interactions ──────────────────────────────────────────
            submitted = False
            for instr in instructions:
                action = instr.get("action", "")
                sel    = instr.get("selector", "")
                try:
                    if action == "fill":
                        page.fill(sel, str(instr.get("value", "")))
                    elif action == "select":
                        try:
                            page.select_option(sel, value=str(instr.get("value", "")))
                        except Exception:
                            page.select_option(sel, label=str(instr.get("value", "")))
                    elif action == "upload" and instr.get("file") == "cv":
                        if cv_path and os.path.exists(cv_path):
                            page.set_input_files(sel, cv_path)
                    elif action == "check":
                        page.check(sel)
                    elif action == "click":
                        page.click(sel)
                    elif action == "submit":
                        page.click(sel)
                        submitted = True
                        time.sleep(0.2)
                except Exception as e:
                    print(f"[apply-engine] instr {instr}: {e}")

            # Fallback submit if explicit action missed
            if not submitted:
                for s in ['button[type="submit"]', 'input[type="submit"]',
                          'button:has-text("Submit")', 'button:has-text("Apply")']:
                    try:
                        page.click(s, timeout=3_000)
                        submitted = True
                        break
                    except Exception:
                        continue

            if not submitted:
                browser.close()
                ft, fd = _classify_failure("Could not find or click submit button")
                return {**_base, "apply_failure_type": ft, "apply_failure_detail": fd,
                        "error": "Could not find or click submit button"}

            # 6. Wait for result page ──────────────────────────────────────────
            try:
                page.wait_for_load_state("networkidle", timeout=12_000)
            except PWTimeout:
                pass

            result_text = page.evaluate("document.body.innerText") or ""
            result_url  = page.url

            # 7. Verify confirmation ───────────────────────────────────────────
            phrase_ok = any(p in result_text.lower() for p in _CONFIRMATION_PHRASES)
            try:
                v = _claude_json(
                    f'After submitting application for "{job_title}" at "{company}":\n'
                    f'URL: {result_url}\nPage: {result_text[:2000]}\n\n'
                    f'Was the application successfully received by the company?\n'
                    f'Return JSON: {{"confirmed":true/false,"message":"confirmation or reason"}}',
                    max_tokens=200,
                )
                confirmed = bool(v.get("confirmed", False)) or phrase_ok
                msg = v.get("message", result_text[:400])
            except Exception:
                confirmed = phrase_ok
                msg = result_text[:400]

            browser.close()
            return {
                "success": confirmed,
                "status": "confirmed" if confirmed else "submitted",
                "confirmation_text": msg,
                "error": "",
                "resolved_url": actual_url,
            }

    except Exception as e:
        err = str(e)
        ft, fd = _classify_failure(err)
        status = "manual_required" if ft in ("captcha", "login_wall") else "failed"
        return {**_base, "status": status, "error": err,
                "apply_failure_type": ft, "apply_failure_detail": fd}
