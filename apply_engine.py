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

def _is_workday(url: str) -> bool:
    """Check if URL is a Workday job board."""
    return bool(re.search(r'myworkdayjobs\.com|\.myworkday\.com/.*?/job/', url))

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
        host = urllib.parse.urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
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
                timeout=30_000,  # fail fast if Chromium can't start (host OOM, missing deps)
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

_DEBUG_DIR = os.environ.get("APPLY_DEBUG_DIR", "")


def _save_debug_shot(page, tag: str = "apply_fail") -> str:
    """Best-effort full-page screenshot on failure. No-op unless APPLY_DEBUG_DIR
    is set (screenshots don't persist without a mounted volume). Returns path."""
    if not _DEBUG_DIR:
        return ""
    try:
        os.makedirs(_DEBUG_DIR, exist_ok=True)
        p = os.path.join(_DEBUG_DIR, f"{tag}_{int(time.time())}.png")
        page.screenshot(path=p, full_page=True)
        return p
    except Exception:
        return ""


def _verify_submission(page) -> dict:
    """Determine the TRUE outcome after clicking a submit button.

    Only declares success on real evidence, and detects validation errors so a
    blocked submit is reported as `failed` (not a false `submitted`). Order:
      1. confirmation phrase -> confirmed
      2. visible required/invalid-field errors -> failed (submit was blocked)
      3. the form disappeared -> submitted (accepted, no explicit thank-you)
      4. same page, form still present, no error -> failed (needs manual review)
    """
    try:
        page.wait_for_load_state("networkidle", timeout=5_000)
    except Exception:
        pass
    text = (page.evaluate("document.body.innerText") or "")
    low = text.lower()
    if any(p in low for p in _CONFIRMATION_PHRASES):
        return {"success": True, "status": "confirmed",
                "confirmation_text": text[:500], "error": ""}
    # Validation errors => submit did NOT go through
    errs = []
    for sel in ('[aria-invalid="true"]', '.field_with_errors', '.error',
                '[class*="error"]', '[role="alert"]'):
        try:
            for e in (page.query_selector_all(sel) or [])[:6]:
                t = (e.inner_text() or "").strip()
                if t and len(t) < 140:
                    errs.append(t)
        except Exception:
            continue
    errs = [x for i, x in enumerate(dict.fromkeys(errs)) if i < 6]
    if errs:
        _shot = _save_debug_shot(page, "apply_validation")
        return {"success": False, "status": "failed", "confirmation_text": "",
                "error": ("Submit blocked by required/invalid fields: " + "; ".join(errs)
                          + (f" [shot: {_shot}]" if _shot else ""))}
    still_form = True
    try:
        still_form = bool(page.query_selector("form"))
    except Exception:
        pass
    if not still_form:
        return {"success": True, "status": "submitted",
                "confirmation_text": text[:500], "error": ""}
    _shot = _save_debug_shot(page, "apply_noconfirm")
    return {"success": False, "status": "failed", "confirmation_text": text[:300],
            "error": ("No confirmation after submit (form still present) — needs manual review"
                      + (f" [shot: {_shot}]" if _shot else ""))}


COLLECT_REQUIRED_JS = '() => {\n  const out = [];\n  const seen = new Set();\n  const els = document.querySelectorAll(\'input, select, textarea\');\n  for (const el of els) {\n    const type = (el.type || el.tagName).toLowerCase();\n    if (type === \'hidden\' || el.disabled || el.readOnly) continue;\n    const required = el.required || el.getAttribute(\'aria-required\') === \'true\';\n    const invalid = el.getAttribute(\'aria-invalid\') === \'true\';\n    const empty = !el.value || String(el.value).trim() === \'\';\n    const isChoice = (type === \'radio\' || type === \'checkbox\');\n    if (!invalid && !(required && (empty || isChoice))) continue;\n    let label = \'\';\n    if (el.id) { const l = document.querySelector(\'label[for="\' + CSS.escape(el.id) + \'"]\'); if (l) label = l.innerText; }\n    if (!label && el.closest(\'label\')) label = el.closest(\'label\').innerText;\n    if (!label) label = el.getAttribute(\'aria-label\') || el.name || el.placeholder || \'\';\n    label = (label || \'\').replace(/\\s+/g, \' \').trim().slice(0, 140);\n    let sel = \'\';\n    if (el.id) sel = \'#\' + CSS.escape(el.id);\n    else if (el.name) sel = el.tagName.toLowerCase() + \'[name="\' + el.name + \'"]\';\n    else continue;\n    const key = sel + \'|\' + (el.value || \'\');\n    if (seen.has(key)) continue; seen.add(key);\n    let options = [];\n    if (el.tagName === \'SELECT\') options = [...el.options].map(o => o.value || o.text).filter(Boolean).slice(0, 25);\n    if (type === \'radio\') { const g = document.getElementsByName(el.name); options = [...g].map(r => r.value).filter(Boolean).slice(0, 25); }\n    out.push({ selector: sel, label: label, type: type, options: options });\n    if (out.length >= 15) break;\n  }\n  return out;\n}'


def _collect_unfilled_required(page):
    """Return a compact list of still-required / invalid form fields the standard
    handlers did not fill (custom screening questions, EEO, work-auth, etc.)."""
    try:
        fields = page.evaluate(COLLECT_REQUIRED_JS) or []
    except Exception:
        return []
    # de-dup by selector, keep order
    seen, out = set(), []
    for fdef in fields:
        sel = (fdef or {}).get("selector")
        if not sel or sel in seen:
            continue
        seen.add(sel)
        out.append(fdef)
    return out[:15]


def _answer_and_fill_required(page, fields, applicant) -> int:
    """Ask Gemini to answer the collected required fields, then fill them.
    Conservative: truthful from applicant data; assumes work authorization + no
    sponsorship unless stated; declines demographic/EEO questions when possible."""
    if not fields:
        return 0
    prompt = (
        "You are completing REQUIRED fields on a job application that were left blank.\n"
        "Applicant data (JSON):\n" + json.dumps(applicant) + "\n\n"
        "Fields (JSON array):\n" + json.dumps(fields) + "\n\n"
        "Return ONLY a JSON array of actions: "
        '{"selector": "...", "action": "fill|select|check", "value": "..."}.\n'
        "Rules:\n"
        "- Answer truthfully from the applicant data.\n"
        "- Work authorization: assume the applicant IS authorized to work in the "
        "posting's country and does NOT require visa sponsorship, unless the "
        "applicant data clearly says otherwise.\n"
        "- Demographic / EEO / gender / race / veteran / disability questions: pick a "
        '"decline to self-identify" / "prefer not to say" option when one exists.\n'
        "- Yes/No eligibility questions: choose the answer that keeps the application valid.\n"
        "- For select/radio, value MUST exactly match one of the provided options.\n"
        "- Only include fields you can answer confidently. No prose, JSON only."
    )
    try:
        actions = _claude_json(prompt, max_tokens=1024, timeout=_APPLY_LLM_TIMEOUT)
    except Exception as e:
        print(f"[apply-engine] required-field answer failed: {e}")
        return 0
    if not isinstance(actions, list):
        return 0
    n = 0
    for a in actions:
        if not isinstance(a, dict):
            continue
        sel = a.get("selector", ""); act = a.get("action", "fill"); val = str(a.get("value", ""))
        if not sel:
            continue
        try:
            if act == "select":
                try:
                    page.select_option(sel, value=val)
                except Exception:
                    page.select_option(sel, label=val)
            elif act == "check":
                try:
                    page.check(sel)
                except Exception:
                    page.check(sel + '[value="' + val + '"]')
            else:
                page.fill(sel, val)
            n += 1
        except Exception as e:
            print(f"[apply-engine] required-field fill {sel}: {e}")
            continue
    return n


def _finalize_after_submit(page, applicant, submit_selectors) -> dict:
    """Verify the outcome; if the submit was blocked by required/invalid fields,
    auto-answer them once and resubmit. Bounded to a single recovery pass."""
    res = _verify_submission(page)
    if res.get("status") != "failed":
        return res
    try:
        fields = _collect_unfilled_required(page)
        if fields:
            n = _answer_and_fill_required(page, fields, applicant)
            if n:
                for sel in submit_selectors:
                    try:
                        page.click(sel, timeout=5_000)
                        break
                    except Exception:
                        continue
                res2 = _verify_submission(page)
                res2["confirmation_text"] = (
                    (res2.get("confirmation_text", "") or "")
                    + f" [auto-answered {n} required field(s)]")[:600]
                return res2
    except Exception as e:
        res["error"] = ((res.get("error", "") or "") + f" | required-retry error: {e}")[:480]
    return res


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
                    page.wait_for_load_state("networkidle", timeout=4_000)
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

        return _finalize_after_submit(page, applicant,
            ['button[type="submit"]', 'input[type="submit"]',
             'button:has-text("Submit")', 'button:has-text("Apply")'])
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
                    page.wait_for_load_state("networkidle", timeout=4_000)
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

        return _finalize_after_submit(page, applicant,
            ['button[type="submit"]', 'button:has-text("Submit")',
             'a:has-text("Submit")', '.postings-btn'])
    except Exception as e:
        result["error"] = f"Lever handler error: {e}"
        return result

def _apply_workday(page, applicant: dict, cv_path: str | None) -> dict:
    """Handle Workday multi-page application wizard.

    Workday uses a step-by-step form (up to ~10 pages). We fill whatever
    fields we can on each page, then click Next until we hit Submit.
    """
    result = {"success": False, "status": "failed", "confirmation_text": "", "error": ""}

    # Automation IDs used by Workday's React components
    _NEXT_SELS = [
        'button[data-automation-id="bottom-navigation-next-button"]',
        'button[data-automation-id="nextButton"]',
        'button:has-text("Next")',
        'button:has-text("Continue")',
        'button:has-text("Save and Continue")',
    ]
    _SUBMIT_SELS = [
        'button[data-automation-id="bottom-navigation-footer-button"]',
        'button[data-automation-id="submitButton"]',
        'button:has-text("Submit")',
        'button[aria-label*="submit" i]',
    ]

    try:
        filled_cv = False
        for page_num in range(12):  # hard cap: prevent infinite loops
            try:
                page.wait_for_load_state("networkidle", timeout=4_000)
            except Exception:
                pass

            page_text = page.evaluate("document.body.innerText") or ""
            lower = page_text.lower()

            # ── Confirmation ──────────────────────────────────────────────
            if any(p in lower for p in _CONFIRMATION_PHRASES):
                result.update(success=True, status="confirmed",
                               confirmation_text=page_text[:500])
                return result

            # ── Login wall ────────────────────────────────────────────────
            if any(p in lower for p in _BLOCKER_PHRASES):
                result.update(status="manual_required",
                               error="Workday: login or account required")
                return result

            # ── Fill fields on current page ───────────────────────────────
            _gh_fill(page,
                'input[data-automation-id="legalNameSection_firstName"],'
                'input[placeholder*="First" i],input[name*="firstName"]',
                applicant.get('first_name', ''))
            _gh_fill(page,
                'input[data-automation-id="legalNameSection_lastName"],'
                'input[placeholder*="Last" i],input[name*="lastName"]',
                applicant.get('last_name', ''))
            _gh_fill(page,
                'input[data-automation-id="email"],input[type="email"]',
                applicant.get('email', ''))
            _gh_fill(page,
                'input[data-automation-id*="phone"],input[type="tel"]',
                applicant.get('phone', ''))
            _gh_fill(page,
                'input[data-automation-id*="address"],input[placeholder*="Address" i]',
                applicant.get('location', ''))

            # ── Upload CV (once) ──────────────────────────────────────────
            if not filled_cv and cv_path:
                for sel in [
                    'input[data-automation-id="file-upload-input-ref"]',
                    'input[type="file"][name*="resume" i]',
                    'input[type="file"]',
                ]:
                    try:
                        page.set_input_files(sel, cv_path)
                        filled_cv = True
                        print(f"[apply-engine] Workday: uploaded CV via {sel}")
                        break
                    except Exception:
                        continue

            # ── Try Submit first ──────────────────────────────────────────
            for sel in _SUBMIT_SELS:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible() and el.is_enabled():
                        el.click()
                        try:
                            page.wait_for_load_state("networkidle", timeout=5_000)
                        except Exception:
                            pass
                        confirm = page.evaluate("document.body.innerText") or ""
                        if any(p in confirm.lower() for p in _CONFIRMATION_PHRASES):
                            result.update(success=True, status="confirmed",
                                           confirmation_text=confirm[:500])
                        else:
                            result.update(success=True, status="submitted",
                                           confirmation_text=confirm[:500])
                        return result
                except Exception:
                    continue

            # ── Advance to next page ──────────────────────────────────────
            advanced = False
            for sel in _NEXT_SELS:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible() and el.is_enabled():
                        el.click()
                        advanced = True
                        break
                except Exception:
                    continue

            if not advanced:
                break  # no next / submit found — give up

        result["error"] = "Workday: could not complete multi-page form after 12 pages"
        return result

    except Exception as e:
        result["error"] = f"Workday handler error: {e}"
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

# -- Apply retry policy --
MAX_APPLY_ATTEMPTS = 3

# Per-call Gemini timeout used INSIDE the browser apply flow. Kept short so the
# nav-check + form-fill + confirmation calls plus page navigation can never blow
# the overall apply deadline (APPLY_DEADLINE_S, default 120s). Non-apply Gemini
# callers (search/scoring) keep the generous 90s default.
_APPLY_LLM_TIMEOUT = int(os.environ.get('APPLY_LLM_TIMEOUT', '25'))
RETRYABLE_FAILURES = {"timeout", "network_error", "other"}
MANUAL_FAILURES = {"captcha", "login_wall", "form_validation"}

def retry_decision(failure_type, attempts, max_attempts=MAX_APPLY_ATTEMPTS):
    """Decide what to do after a failed apply attempt.

    `attempts` is the attempt count including the one that just failed.
    Returns (action, backoff_seconds):
      'manual' -> needs a human (captcha / login wall / form validation); never auto-retry
      'giveup' -> attempts exhausted; stop retrying
      'retry'  -> transient failure; auto-retry after backoff_seconds
    """
    if failure_type in MANUAL_FAILURES:
        return ("manual", 0)
    if attempts >= max_attempts:
        return ("giveup", 0)
    backoff = min(300 * (4 ** max(0, attempts - 1)), 6 * 3600)
    return ("retry", backoff)


ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI_KEY", "")

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

def _claude(prompt: str, max_tokens: int = 1024, timeout: int = 90) -> str:
    """Call Gemini Flash and return the text response.

    Kept named _claude for backward-compat with every apply-engine caller; the
    app is Gemini-only (no Anthropic key required)."""
    key = GEMINI_KEY or os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GEMINI_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not configured")
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        # gemini-2.5-flash "thinking" otherwise eats the output budget and the
        # call returns prose with no JSON (e.g. extract_applicant_data: "No JSON
        # found"). Disable thinking so the structured output comes back.
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=" + key,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    text = result["candidates"][0]["content"]["parts"][0]["text"].strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    return text.strip()

def _claude_json(prompt: str, max_tokens: int = 1024, timeout: int = 90):
    """Call Gemini and parse the JSON response."""
    text = _claude(prompt, max_tokens, timeout=timeout)
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
    if not cv_text or not GEMINI_KEY:
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

# ── Parked / expired-domain detection ─────────────────────────────────────────
# An expired or unregistered domain often gets caught by a registrar/parking
# service that returns HTTP 200 with a "this domain is for sale" landing page
# (GoDaddy, Sedo, Afternic, Dan, HugeDomains, …). Those must count as DEAD even
# though the status code is < 400, otherwise a dead posting looks "Verified".
_PARKED_HOSTS = (
    "sedoparking.com", "sedo.com", "forsale.godaddy.com", "godaddy.com/forsale",
    "afternic.com", "dan.com", "hugedomains.com", "bodis.com", "parkingcrew.net",
    "above.com", "uniregistry.com", "buydomains.com", "domainmarket.com",
    "cashparking.com", "parklogic.com", "smartname.com", "domize.com",
    "undeveloped.com", "namebright.com/parking", "sav.com/parking",
)
_PARKED_PHRASES = (
    "the domain name", "is for sale", "buy this domain", "this domain is for sale",
    "domain may be for sale", "checkout the full domain", "make an offer",
    "domain parking", "get this domain", "interested in this domain",
    "the domain you are looking for", "this webpage was generated by the domain owner",
    "verified domain", "own it today",
)


_LANDER_SIGNS = ("/lander", "_lander", "/park", "/caf/", "sedoparking",
                 "parkingcrew", "bodis", "afternic", "cashparking", "/_ds/")


# Phrases that appear on a job page whose posting is CLOSED / FILLED even though
# the URL still returns HTTP 200 (Greenhouse/Lever/Comeet/Ashby/Workday all do
# this). A LIVE posting essentially never contains these, so a single strong
# match is treated as "the job is gone". Kept tight to avoid false positives.
_CLOSED_PHRASES = (
    "no longer accepting applications",
    "no longer accepting application",
    "this position has been filled",
    "this job has been filled",
    "position has been closed",
    "this posting is closed",
    "posting is no longer available",
    "job posting is no longer available",
    "this job is no longer available",
    "this position is no longer available",
    "the job you are looking for is no longer",
    "this role is no longer open",
    "we are no longer accepting applications",
    "applications are no longer being accepted",
    "job is not available anymore",
    "this opening has been filled",
    "position is no longer open",
    "this listing has expired",
    "job listing has expired",
    "the page you were looking for doesn't exist",
    "job not found",
    "position not found",
    "oops! that job",
)


def _looks_closed(body: str) -> bool:
    """True if a 200-OK page reads as a closed/filled/expired posting.

    Matches on the VISIBLE text only (scripts/styles stripped) so JS strings
    can't trigger it, and requires an exact known-closed phrase."""
    if not body:
        return False
    _stripped = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", body)
    visible = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _stripped)).strip().lower()
    if not visible:
        return False
    return any(ph in visible for ph in _CLOSED_PHRASES)


def _looks_parked(final_url: str, body: str) -> bool:
    """Heuristic: does this look like a parked / for-sale domain page?

    Catches three shapes:
      1. Final URL on a known parking/for-sale host (definitive).
      2. Body carrying 2+ for-sale phrases (GoDaddy/Sedo landers).
      3. A tiny client-side-redirect stub (`window.location=... "/lander"`) —
         modern parking services cloak behind a JS/meta redirect, so a 3-line
         page that only redirects (esp. to a parking lander) is treated as dead.
    A real job posting is never any of these."""
    fu = (final_url or "").lower()
    if any(h in fu for h in _PARKED_HOSTS):
        return True
    b = body or ""
    bl = b.lower()
    if sum(1 for p in _PARKED_PHRASES if p in bl) >= 2:
        return True
    # Client-side redirect stub detection
    targets = re.findall(r"""location\.(?:href|replace)\s*(?:=|\()\s*['"]([^'"]+)['"]""", bl)
    targets += re.findall(r"""http-equiv=["']refresh["'][^>]*url=['"]?([^'"\s>]+)""", bl)
    redirect_target = " ".join(targets)
    has_redirect = bool(targets) or ("window.location" in bl) or ('http-equiv="refresh"' in bl)
    if has_redirect and any(sig in (redirect_target + " " + fu) for sig in _LANDER_SIGNS):
        return True
    # A stub whose only purpose is to redirect, with essentially no visible text.
    # Strip <script>/<style> blocks first so JS redirect code isn't counted as text.
    _stripped = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", b)
    visible = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _stripped)).strip()
    if has_redirect and len(visible) < 40 and len(b) < 1200:
        return True
    return False


def check_url_alive(url: str, timeout: int = 8) -> bool:
    """Return True if the URL responds with a non-error status AND is not a
    parked / for-sale domain page (which return 200 but the job is gone)."""
    if not url:
        return False
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status >= 400:
                return False
            body = r.read(40000).decode("utf-8", "ignore")
            if _looks_parked(r.geturl(), body):
                return False
            if _looks_closed(body):
                return False
            return True
    except urllib.error.HTTPError as e:
        return e.code < 400
    except Exception:
        return False

# ── Deterministic ATS resolver (steps 2–4: company → career board → posting) ──
# Given a company + job title, resolve to the company's OWN ATS application URL
# by probing the known ATS JSON APIs directly (Greenhouse / Lever / SmartRecruiters
# / Ashby / Comeet) and matching the posting by title. No Google scraping — this
# is deterministic and fast, and reuses the same endpoints the search uses.

try:
    from rapidfuzz import fuzz as _rf

    def _title_ratio(a: str, b: str) -> float:
        return float(_rf.token_set_ratio(a, b))
except Exception:  # pragma: no cover
    from difflib import SequenceMatcher as _SM

    def _title_ratio(a: str, b: str) -> float:
        return _SM(None, a, b).ratio() * 100.0


_ATS_DIRECT_HOSTS = (
    "boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co",
    "jobs.ashbyhq.com", "jobs.smartrecruiters.com", "comeet.co",
    "myworkdayjobs.com", "eu.greenhouse.io",
)


def _is_direct_ats(url: str) -> bool:
    u = (url or "").lower()
    return any(h in u for h in _ATS_DIRECT_HOSTS)


def _company_slugs(company: str) -> list[str]:
    """Best-effort slug candidates for a company name (deterministic)."""
    base = (company or "").lower().strip()
    if not base:
        return []
    base = re.sub(r"\b(inc|ltd|llc|corp|gmbh|co|the)\b", " ", base)
    base = base.replace("&", "and")
    nodots = re.sub(r"\.(com|io|ai|co|net|org)$", "", base).strip()
    cands = [
        re.sub(r"[^a-z0-9]+", "", nodots),    # "monday.com" -> "monday"
        re.sub(r"[^a-z0-9]+", "", base),      # "mondaycom"
        re.sub(r"[^a-z0-9]+", "-", nodots).strip("-"),  # "palo-alto-networks"
    ]
    out = []
    for c in cands:
        if c and len(c) >= 2 and c not in out:
            out.append(c)
    return out[:3]


def _ats_get(url: str, timeout: int = 6):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status >= 400:
                return None
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return None


def _ats_postings_for_slug(slug: str) -> list[dict]:
    """Return [{title, url, location}] across ATSes for one company slug."""
    out = []
    # Greenhouse
    gh = _ats_get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false")
    if isinstance(gh, dict):
        for j in gh.get("jobs", []) or []:
            loc = j.get("location", {}) or {}
            out.append({"title": j.get("title", ""),
                        "url": j.get("absolute_url") or f"https://boards.greenhouse.io/{slug}/jobs/{j.get('id','')}",
                        "location": loc.get("name", "") if isinstance(loc, dict) else "",
                        "ats": "greenhouse"})
    # Lever
    lv = _ats_get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if isinstance(lv, list):
        for j in lv:
            cats = j.get("categories") or {}
            out.append({"title": j.get("text", ""),
                        "url": j.get("hostedUrl") or f"https://jobs.lever.co/{slug}/{j.get('id','')}",
                        "location": cats.get("location", "") or "",
                        "ats": "lever"})
    # SmartRecruiters
    sr = _ats_get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
    if isinstance(sr, dict):
        for j in sr.get("content", []) or []:
            loc = j.get("location") or {}
            city = loc.get("city", "") if isinstance(loc, dict) else ""
            out.append({"title": j.get("name", ""),
                        "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id','')}",
                        "location": city, "ats": "smartrecruiters"})
    # Ashby
    ab = _ats_get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false")
    if isinstance(ab, dict):
        for j in ab.get("jobs", []) or []:
            out.append({"title": j.get("title", ""),
                        "url": j.get("jobUrl") or j.get("applyUrl") or "",
                        "location": j.get("location", "") or "", "ats": "ashby"})
    # Comeet
    cm = _ats_get(f"https://www.comeet.co/careers/api/{slug}/positions")
    if isinstance(cm, list):
        for j in cm:
            loc = j.get("location")
            loc = (loc.get("name", "") if isinstance(loc, dict) else str(loc or ""))
            out.append({"title": j.get("name", ""), "url": j.get("url", ""),
                        "location": loc, "ats": "comeet"})
    return [p for p in out if p.get("title") and p.get("url")]


def _gemini_board_candidates(company: str, job_title: str = "") -> list[str]:
    """Ask Gemini for likely ATS board SLUGS for a company. Companies routinely
    use a slug that differs from their name (Wiz→"wizinc", Gong→"gongio"), which
    plain name-munging can't produce. Every returned slug is VERIFIED by probing
    the live ATS API, so a wrong/hallucinated guess is harmless (it just yields
    no postings). Off with APPLY_RESOLVE_LLM_SLUGS=0."""
    if not company:
        return []
    if os.environ.get("APPLY_RESOLVE_LLM_SLUGS", "1").strip().lower() in ("0", "false", "no", "off"):
        return []
    if not (GEMINI_KEY or os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI_KEY")):
        return []
    prompt = (
        f'The company "{company}" posts jobs on an ATS (Greenhouse, Lever, Ashby, '
        f'or SmartRecruiters). The board "slug" is the identifier in the URL — e.g. '
        f'boards.greenhouse.io/<slug> or jobs.lever.co/<slug>. Slugs often differ '
        f'from the company name (e.g. Wiz -> "wizinc", Gong -> "gongio"). Return ONLY '
        f'a JSON array of up to 5 most-likely lowercase board slugs for "{company}", '
        f'best guess first. Example: ["wizinc","wiz"].'
    )
    try:
        raw = _claude(prompt, max_tokens=128, timeout=20)
    except Exception as e:
        print(f"[apply-engine] gemini board hint error: {e}")
        return []
    # Shape-tolerant: prefer quoted tokens (JSON array / quoted list); fall back
    # to bare words. Every slug is verified against the live ATS API downstream,
    # so junk candidates are harmless.
    low = (raw or "").lower()
    toks = re.findall(r'"([a-z0-9][a-z0-9\-]{1,40})"', low)
    if not toks:
        toks = re.findall(r'[a-z0-9][a-z0-9\-]{1,40}', low)
    _stop = {"json", "slug", "slugs", "array", "the", "com", "io", "ai", "ats",
             "board", "boards", "greenhouse", "lever", "ashby", "smartrecruiters",
             "comeet", "company", "example", "likely", "https", "http", "www", "careers"}
    out = []
    for t in toks:
        t = re.sub(r"[^a-z0-9-]", "", t).strip("-")
        if t and len(t) >= 2 and t not in out and t not in _stop:
            out.append(t)
    return out[:5]


def _norm_company(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# Curated, VERIFIED company → ATS board slug map (probed live, 2026-07-20). This
# is the deterministic primary source so the resolver does NOT depend on Gemini
# (whose prod key is rate-limited / 429). Extend freely: {normalized company: slug}.
# The slug is probed across all ATSes by _ats_postings_for_slug, so only the slug
# is needed. Add new entries after verifying the board returns postings.
_ATS_BOARD_MAP = {
    "wiz": "wizinc",
    "gong": "gongio",
    "melio": "melio",
    "taboola": "taboola",
    "yotpo": "yotpo",
    "similarweb": "similarweb",
    "lemonade": "lemonade",
    "jfrog": "jfrog",
    "fireblocks": "fireblocks",
    "appsflyer": "appsflyer",
    "payoneer": "payoneer",
    "catonetworks": "catonetworks",
}


def resolve_ats_application(company: str, job_title: str, location: str = "",
                            threshold: float = 82.0, use_llm: bool = True) -> "dict | None":
    """Resolve company + title to a direct-ATS application URL. Returns
    {url, ats, matched_title, score, location} or None. Tries name-derived slugs
    first (free), then Gemini-suggested slugs (verified against the live ATS API).
    Best-effort; no Google scraping."""
    if not company or not job_title:
        return None
    best = {"v": None}

    def _scan(slugs) -> bool:
        for slug in slugs:
            for p in _ats_postings_for_slug(slug):
                score = _title_ratio(job_title, p["title"])
                if best["v"] is None or score > best["v"]["score"]:
                    best["v"] = {"url": p["url"], "ats": p["ats"],
                                 "matched_title": p["title"], "score": round(score, 1),
                                 "location": p.get("location", "")}
            if best["v"] and best["v"]["score"] >= 92:   # confident → stop early
                return True
        return False

    tried = []
    _mapped = _ATS_BOARD_MAP.get(_norm_company(company))
    if _mapped:
        tried.append(_mapped)
    for _s in _company_slugs(company):
        if _s not in tried:
            tried.append(_s)
    strong = _scan(tried)
    # Widen with Gemini-suggested slugs only if name-munging didn't nail it.
    if not strong and use_llm:
        extra = [s for s in _gemini_board_candidates(company, job_title) if s not in tried]
        if extra:
            _scan(extra)

    b = best["v"]
    return b if (b and b["score"] >= threshold) else None


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
    global GEMINI_KEY
    if api_key:
        GEMINI_KEY = api_key

    _base = {
        "success": False, "status": "failed", "confirmation_text": "", "error": "",
        "apply_failure_type": None, "apply_failure_detail": None, "resolved_url": job_url,
    }

    # ── Global kill-switch (2026-07-20) ──────────────────────────────────────
    # Auto-submit apply engine is DISABLED by default. Set Railway env
    # APPLY_ENGINE_ENABLED=1 to re-enable. Reversible with no code change.
    if os.environ.get("APPLY_ENGINE_ENABLED", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return {**_base, "status": "manual_required",
                "error": "Apply engine disabled (set APPLY_ENGINE_ENABLED=1 to re-enable)"}

    if not PLAYWRIGHT_AVAILABLE:
        return {**_base, "status": "manual_required",
                "error": "Playwright not installed — run: playwright install chromium --with-deps"}
    if not job_url:
        return {**_base, "error": "No job URL provided"}

    # ── Resolve job board URLs to company's own career page ──────────────────
    # The Google-scraping resolver (_find_company_apply_url) launches a SECOND
    # headless browser and frequently hangs on Google's bot-challenge page,
    # which is a primary cause of applies getting stuck. It is now OFF by
    # default. Job-board URLs (LinkedIn/Indeed/etc.) almost always require a
    # login to apply anyway, so we return manual_required quickly instead of
    # burning a browser. Set APPLY_RESOLVE_CAREER_PAGE=1 to re-enable.
    actual_url = job_url

    # ── Deterministic ATS resolution (steps 2–4) ─────────────────────────────
    # If not already a clean direct-ATS posting, resolve company → known ATS
    # board → the exact posting by title (no Google scrape). APPLY_RESOLVE_ATS=0
    # disables it.
    if not _is_direct_ats(job_url) and os.environ.get("APPLY_RESOLVE_ATS", "1").strip().lower() in ("1", "true", "yes", "on"):
        try:
            _r = resolve_ats_application(company, job_title)
        except Exception as _re:
            _r = None
            print(f"[apply-engine] ATS resolve error: {_re}")
        if _r and _r.get("url"):
            print(f"[apply-engine] Resolved {company!r}/{job_title!r} -> {_r['ats']} {_r['url']} (score {_r['score']})")
            actual_url = _r["url"]

    # Still a job-board URL and unresolved → manual (login wall), unless the
    # legacy Google career-page resolver is explicitly enabled.
    if _is_job_board(actual_url):
        if os.environ.get("APPLY_RESOLVE_CAREER_PAGE", "") == "1":
            direct = _find_company_apply_url(company, job_title)
            if direct:
                print(f"[apply-engine] Career-page resolver -> {direct}")
                actual_url = direct
        if _is_job_board(actual_url):
            print(f"[apply-engine] Job board URL ({actual_url}) — no direct ATS match, flagging for manual apply")
            return {**_base, "status": "manual_required",
                    "apply_failure_type": "login_wall",
                    "apply_failure_detail": "No direct ATS match found; job-board listing requires manual application",
                    "error": "Job-board listing — apply manually on the source site"}
    _base["resolved_url"] = actual_url

    try:
        with sync_playwright() as pw:
            print(f"[apply-engine] Launching Chromium…")
            browser = pw.chromium.launch(
                headless=True,
                timeout=30_000,  # fail fast if Chromium can't start (host OOM, missing deps)
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
                    page.wait_for_load_state("networkidle", timeout=4_000)
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

            if _is_workday(actual_url):
                print(f"[apply-engine] Detected Workday ATS")
                res = _apply_workday(page, applicant, cv_path)
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
                        timeout=_APPLY_LLM_TIMEOUT,
                    )
                    if nav.get("has_apply_button") and nav.get("button_text"):
                        btn = page.get_by_text(nav["button_text"], exact=False).first
                        if btn:
                            btn.click()
                            try:
                                page.wait_for_load_state("networkidle", timeout=4_000)
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
                        timeout=_APPLY_LLM_TIMEOUT,
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
                page.wait_for_load_state("networkidle", timeout=5_000)
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
                        timeout=_APPLY_LLM_TIMEOUT,
                )
                confirmed = bool(v.get("confirmed", False)) or phrase_ok
                msg = v.get("message", result_text[:400])
            except Exception:
                confirmed = phrase_ok
                msg = result_text[:400]

            browser.close()
            if confirmed:
                return {"success": True, "status": "confirmed",
                        "confirmation_text": msg, "error": "",
                        "resolved_url": actual_url}
            # Not confirmed: report honestly as a retryable failure rather than a
            # false 'submitted' (which app.py would mark as 'applied'). Keeps the
            # "Submitted N" summary truthful.
            return {**_base, "status": "failed",
                    "confirmation_text": msg,
                    "error": "No submission confirmation detected — needs manual review",
                    "resolved_url": actual_url}

    except Exception as e:
        err = str(e)
        ft, fd = _classify_failure(err)
        status = "manual_required" if ft in ("captcha", "login_wall") else "failed"
        return {**_base, "status": status, "error": err,
                "apply_failure_type": ft, "apply_failure_detail": fd}
