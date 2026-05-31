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

try:
    from playwright_stealth import stealth_sync as _stealth_sync
    _STEALTH_AVAILABLE = True
except ImportError:
    _STEALTH_AVAILABLE = False
    def _stealth_sync(page): pass  # no-op fallback


# ── Evidence capture ─────────────────────────────────────────────────────────

import pathlib

def _evidence_dir(user_id, job_id, attempt) -> pathlib.Path:
    """Return (and create) the directory for storing apply evidence."""
    base = pathlib.Path(os.environ.get("EVIDENCE_BASE_DIR", "/data/evidence"))
    d = base / str(user_id) / str(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d

def _screenshot(page, user_id, job_id, attempt: int, label: str) -> str | None:
    """Save a screenshot and return the file path, or None on error."""
    try:
        d = _evidence_dir(user_id, job_id, attempt)
        path = d / f"{attempt}_{label}.png"
        page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception as e:
        print(f"[apply-engine] screenshot failed ({label}): {e}")
        return None

def _dump_html(page, user_id, job_id, attempt: int, label: str) -> str | None:
    """Save a full HTML dump and return the file path, or None on error."""
    try:
        d = _evidence_dir(user_id, job_id, attempt)
        path = d / f"{attempt}_{label}.html"
        path.write_text(page.content(), encoding="utf-8", errors="replace")
        return str(path)
    except Exception as e:
        print(f"[apply-engine] html dump failed ({label}): {e}")
        return None

def _evidence_path_str(user_id, job_id, attempt: int) -> str:
    """Return the evidence directory path as a string."""
    try:
        return str(_evidence_dir(user_id, job_id, attempt))
    except Exception:
        return ""


# ── CAPTCHA / login-wall detection ────────────────────────────────────────────

def _detect_blocker(page) -> tuple[str | None, str | None]:
    """
    Pre-submit check for hard blockers.
    Returns (failure_type, detail) or (None, None) if clear.
    failure_type: 'captcha' | 'login_wall' | None
    """
    try:
        # CAPTCHA widgets
        captcha_selectors = [
            'iframe[src*="recaptcha"]',
            'iframe[src*="hcaptcha"]',
            'iframe[src*="challenges.cloudflare.com"]',
            '#cf-challenge-running',
            '#cf-error-details',
            '[data-sitekey]',
            '.g-recaptcha',
            '.h-captcha',
        ]
        for sel in captcha_selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    return 'captcha', f'Detected: {sel}'
            except Exception:
                continue

        # Cloudflare JS challenge (no iframe, just page text)
        try:
            body_text = page.evaluate("document.body.innerText") or ""
            lower = body_text.lower()
            if any(p in lower for p in (
                'verify you are human', 'checking your browser',
                'enable javascript and cookies', 'cloudflare ray id',
            )):
                return 'captcha', 'Cloudflare challenge page detected'
        except Exception:
            pass

        # Login wall — visible password field OR login-phrase in text
        try:
            pw_el = page.query_selector('input[type="password"]')
            if pw_el and pw_el.is_visible():
                return 'login_wall', 'Password field visible — login required'
        except Exception:
            pass

        _login_phrases = [
            'sign in to apply', 'log in to apply', 'login to apply',
            'create an account to apply', 'register to apply',
            'please log in', 'please sign in',
            # Hebrew
            'התחבר כדי להגיש', 'היכנס כדי להגיש',
            # redirect clues
            '/login', '/signin', '/sign-in', '/account/login',
        ]
        try:
            page_url = page.url.lower()
            page_text_lower = (page.evaluate("document.body.innerText") or "").lower()
            for phrase in _login_phrases:
                if phrase in page_text_lower or phrase in page_url:
                    return 'login_wall', f'Login phrase detected: {phrase[:60]}'
        except Exception:
            pass

    except Exception:
        pass

    return None, None


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

def _is_ashby(url: str) -> bool:
    """Check if URL is an Ashby-hosted job page."""
    return bool(re.search(r'jobs\.ashbyhq\.com|ashbyhq\.com/.*?/application', url))

def _is_smartrecruiters(url: str) -> bool:
    """Check if URL is a SmartRecruiters job page."""
    return bool(re.search(r'jobs\.smartrecruiters\.com', url))

def _is_bamboohr(url: str) -> bool:
    """Check if URL is a BambooHR job page."""
    return bool(re.search(r'\.bamboohr\.com/careers|bamboohr\.com/jobs', url))

def _is_icims(url: str) -> bool:
    """Check if URL is an iCIMS job page."""
    return bool(re.search(r'\.icims\.com/jobs|careers\.icims\.com', url))

def _is_comeet(url: str) -> bool:
    """Check if URL is a Comeet job page."""
    return bool(re.search(r'comeet\.com/jobs|comeet\.co/jobs', url))

def _is_jobvite(url: str) -> bool:
    """Check if URL is a Jobvite job page."""
    return bool(re.search(r'jobs\.jobvite\.com|app\.jobvite\.com', url))

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

# ── Known ATS domains (shared across resolver tiers) ─────────────────────────
_ATS_DOMAINS = [
    'greenhouse.io', 'lever.co', 'myworkdayjobs.com', 'myworkday.com',
    'ashbyhq.com', 'bamboohr.com', 'smartrecruiters.com',
    'icims.com', 'taleo.net', 'successfactors.com', 'jobvite.com',
    'comeet.com', 'comeet.co',
]

def _is_ats_url(url: str) -> bool:
    return any(d in url for d in _ATS_DOMAINS)

def _verify_url(url: str, timeout: int = 6) -> bool:
    """HEAD/GET check that a URL is reachable."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status < 400
    except urllib.error.HTTPError as e:
        return e.code < 400
    except Exception:
        return False

def _extract_ats_links(html: str) -> list[str]:
    """Pull all href links from raw HTML that look like ATS or career URLs."""
    links = re.findall(r'href=["\']?(https?://[^\s"\'<>]+)', html)
    good = []
    for link in links:
        if any(d in link for d in _ATS_DOMAINS):
            good.append(link)
    return good

# ── Tier 2: DuckDuckGo HTML search ───────────────────────────────────────────

def _ddg_search(company: str, job_title: str) -> str | None:
    """Search DuckDuckGo HTML endpoint (no JS, much less CAPTCHA than Google)."""
    query = f'"{company}" "{job_title}" site:greenhouse.io OR site:lever.co OR site:ashbyhq.com OR site:bamboohr.com OR site:smartrecruiters.com OR site:workday.com'
    url   = f"https://duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=12) as r:
            html = r.read().decode("utf-8", errors="replace")
        links = re.findall(r'href=["\']?(https?://[^\s"\'<>]+)', html)
        for link in links:
            if _is_ats_url(link) and not _is_job_board(link):
                print(f"[resolver] DDG found: {link}")
                return link
        # Broader fallback: any career URL
        for link in links:
            if any(kw in link.lower() for kw in ["career", "job", "apply"]):
                if not _is_job_board(link):
                    return link
    except Exception as e:
        print(f"[resolver] DDG error: {e}")
    return None

# ── Tier 3: Gemini grounding ──────────────────────────────────────────────────

def _gemini_resolve(company: str, job_title: str, api_key: str = "") -> str | None:
    """Ask Gemini (with Google Search grounding) for the direct apply URL."""
    key = api_key or os.environ.get("GEMINI_KEY") or os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        return None
    payload = json.dumps({
        "contents": [{"parts": [{"text":
            f'What is the direct ATS application URL for the "{job_title}" role at "{company}"? '
            f'Return ONLY a single URL (starting with https://) for an ATS like Greenhouse, Lever, Workday, Ashby, or the company careers page. No explanation.'
        }]}],
        "tools": [{"google_search": {}}],
    }).encode()
    try:
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        # Extract first URL from the response
        m = re.search(r'https?://[^\s"\'<>]+', text)
        if m:
            url = m.group(0).rstrip(".")
            if _verify_url(url):
                print(f"[resolver] Gemini found: {url}")
                return url
    except Exception as e:
        print(f"[resolver] Gemini error: {e}")
    return None

# ── Tier 4: Company homepage scrape ──────────────────────────────────────────

def _homepage_scrape(company: str, job_title: str) -> str | None:
    """Try common career page patterns on the company's own domain."""
    slug = re.sub(r'[^a-z0-9]+', '-', company.lower()).strip('-')
    candidates = [
        f"https://{slug}.com/careers",
        f"https://{slug}.com/jobs",
        f"https://{slug}.io/careers",
        f"https://careers.{slug}.com",
        f"https://jobs.{slug}.com",
    ]
    for base_url in candidates:
        try:
            req = urllib.request.Request(base_url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=8) as r:
                if r.status >= 400:
                    continue
                html = r.read().decode("utf-8", errors="replace")
            # Look for a link matching the job title
            title_slug = re.sub(r'[^a-z0-9]+', '.', job_title.lower())
            pattern    = re.compile(title_slug[:20], re.IGNORECASE)
            links      = re.findall(r'href=["\']?(https?://[^\s"\'<>]+|/[^\s"\'<>]*)', html)
            for link in links:
                if pattern.search(link):
                    full = link if link.startswith("http") else base_url.rstrip("/careers/jobs") + link
                    print(f"[resolver] Homepage scrape found: {full}")
                    return full
            # If we found the careers page itself, return it as fallback
            print(f"[resolver] Homepage careers page: {base_url}")
            return base_url
        except Exception:
            continue
    return None

# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_get(company: str, job_title: str) -> str | None:
    """Return cached resolved URL if < 7 days old, else None."""
    try:
        import sqlite3 as _sq
        db_path = os.environ.get("DB_PATH") or "/data/jobs.db"
        conn = _sq.connect(db_path)
        conn.row_factory = _sq.Row
        from datetime import timedelta as _td2
        cutoff = (datetime.now() - _td2(days=7)).isoformat()
        row = conn.execute(
            "SELECT resolved_url FROM career_url_cache WHERE company=? AND job_title=? AND created_date>=?",
            (company, job_title, cutoff)
        ).fetchone()
        conn.close()
        return row["resolved_url"] if row else None
    except Exception:
        return None

def _cache_set(company: str, job_title: str, url: str):
    """Upsert a resolved URL into the cache."""
    try:
        import sqlite3 as _sq
        db_path = os.environ.get("DB_PATH") or "/data/jobs.db"
        conn = _sq.connect(db_path)
        conn.execute(
            "INSERT OR REPLACE INTO career_url_cache (company, job_title, resolved_url, created_date) VALUES (?,?,?,?)",
            (company, job_title, url, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

# ── Master resolver ───────────────────────────────────────────────────────────

def _find_company_apply_url(company: str, job_title: str) -> str | None:
    """
    4-tier career page resolver (replaces Google-only scrape):
      Tier 0 — 7-day DB cache
      Tier 1 — Direct ATS URL check (if the job URL already IS an ATS URL, caller skips this)
      Tier 2 — DuckDuckGo HTML search (far less CAPTCHA than Google)
      Tier 3 — Gemini Google Search grounding
      Tier 4 — Company homepage scrape
    """
    # Tier 0: cache
    cached = _cache_get(company, job_title)
    if cached:
        print(f"[resolver] Cache hit: {cached}")
        return cached

    result = None

    # Tier 2: DuckDuckGo
    result = _ddg_search(company, job_title)

    # Tier 3: Gemini (if DDG failed)
    if not result:
        result = _gemini_resolve(company, job_title)

    # Tier 4: Homepage scrape (last resort)
    if not result:
        result = _homepage_scrape(company, job_title)

    if result:
        _cache_set(company, job_title, result)

    return result

# ── ATS-specific form handlers ────────────────────────────────────────────────

def _gh_fill(page, selector: str, value: str):
    """Safely fill a form field, skipping if not found or empty value."""
    if not value:
        return
    try:
        el = page.query_selector(selector)
        if el and el.is_visible():
            _human_delay(0.05, 0.18)
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

        # CAPTCHA / login-wall check before submitting
        bt, bd = _detect_blocker(page)
        if bt:
            result.update(status="manual_required", apply_failure_type=bt,
                          apply_failure_detail=bd, error=f"Greenhouse blocker: {bd}")
            return result

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

        # CAPTCHA / login-wall check before submitting
        bt, bd = _detect_blocker(page)
        if bt:
            result.update(status="manual_required", apply_failure_type=bt,
                          apply_failure_detail=bd, error=f"Lever blocker: {bd}")
            return result

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
                page.wait_for_load_state("networkidle", timeout=8_000)
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

            # ── CAPTCHA / login-wall check on each page ──────────────────
            wbt, wbd = _detect_blocker(page)
            if wbt:
                result.update(status="manual_required", apply_failure_type=wbt,
                              apply_failure_detail=wbd, error=f"Workday blocker: {wbd}")
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
                            page.wait_for_load_state("networkidle", timeout=12_000)
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



# ── Named ATS handlers — Issue #9 ────────────────────────────────────────────

def _apply_ashby(page, applicant: dict, cv_path) -> dict:
    result = {"success": False, "status": "failed", "confirmation_text": "", "error": ""}
    try:
        for sel in ['a[data-testid="apply-button"]','button[data-testid="apply-button"]',
                    'a:has-text("Apply")','button:has-text("Apply")']:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible(): el.click(); page.wait_for_load_state("networkidle",timeout=8000); break
            except: continue
        _gh_fill(page,'input[name="name"],input[placeholder*="Name" i]',
                 f"{applicant.get('first_name','')} {applicant.get('last_name','')}".strip())
        _gh_fill(page,'input[name="email"],input[type="email"]',applicant.get('email',''))
        _gh_fill(page,'input[name="phone"],input[type="tel"]',applicant.get('phone',''))
        _gh_fill(page,'input[name*="linkedin" i],input[placeholder*="LinkedIn" i]',applicant.get('linkedin_url',''))
        _gh_fill(page,'input[name*="github" i],input[placeholder*="GitHub" i]',applicant.get('github_url',''))
        _gh_fill(page,'input[name*="location" i],input[name*="city" i]',applicant.get('location',''))
        if cv_path:
            for sel in ['input[type="file"]','input[name*="resume" i]','input[name*="cv" i]']:
                try: page.set_input_files(sel,cv_path); print("[apply-engine] Ashby: uploaded CV"); break
                except: continue
        bt,bd = _detect_blocker(page)
        if bt: result.update(status="manual_required",apply_failure_type=bt,apply_failure_detail=bd,error=f"Ashby blocker: {bd}"); return result
        for sel in ['button[type="submit"]','button:has-text("Submit Application")','button:has-text("Submit")','button:has-text("Apply")']:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible(): el.click(); page.wait_for_load_state("networkidle",timeout=10000); break
            except: continue
        c = page.evaluate("document.body.innerText") or ""
        if any(w in c.lower() for w in ["thank","received","submitted","application"]):
            result.update(success=True,status="confirmed",confirmation_text=c[:500])
        else:
            result.update(success=True,status="submitted",confirmation_text=c[:500])
        return result
    except Exception as e:
        result["error"] = f"Ashby handler error: {e}"; return result


def _apply_smartrecruiters(page, applicant: dict, cv_path) -> dict:
    result = {"success": False, "status": "failed", "confirmation_text": "", "error": ""}
    try:
        for sel in ['a.btn-apply','a[data-qa="btn-apply"]','button[data-qa="btn-apply"]',
                    'a:has-text("Apply Now")','button:has-text("Apply Now")']:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible(): el.click(); page.wait_for_load_state("networkidle",timeout=8000); break
            except: continue
        _gh_fill(page,'[data-qa="firstName"],input[name="firstName"]',applicant.get('first_name',''))
        _gh_fill(page,'[data-qa="lastName"],input[name="lastName"]',applicant.get('last_name',''))
        _gh_fill(page,'[data-qa="email"],input[name="email"],input[type="email"]',applicant.get('email',''))
        _gh_fill(page,'[data-qa="phone"],input[name="phone"],input[type="tel"]',applicant.get('phone',''))
        _gh_fill(page,'input[name*="location" i],input[name*="city" i]',applicant.get('location',''))
        if cv_path:
            for sel in ['input[type="file"]','[data-qa="file-upload"]']:
                try: page.set_input_files(sel,cv_path); print("[apply-engine] SmartRecruiters: uploaded CV"); break
                except: continue
        bt,bd = _detect_blocker(page)
        if bt: result.update(status="manual_required",apply_failure_type=bt,apply_failure_detail=bd,error=f"SmartRecruiters blocker: {bd}"); return result
        for sel in ['[data-qa="btn-submit"]','button[type="submit"]',
                    'button:has-text("Send Application")','button:has-text("Submit")']:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible(): el.click(); page.wait_for_load_state("networkidle",timeout=10000); break
            except: continue
        c = page.evaluate("document.body.innerText") or ""
        if any(w in c.lower() for w in ["thank","received","submitted","application"]):
            result.update(success=True,status="confirmed",confirmation_text=c[:500])
        else:
            result.update(success=True,status="submitted",confirmation_text=c[:500])
        return result
    except Exception as e:
        result["error"] = f"SmartRecruiters handler error: {e}"; return result


def _apply_bamboohr(page, applicant: dict, cv_path) -> dict:
    result = {"success": False, "status": "failed", "confirmation_text": "", "error": ""}
    try:
        for sel in ['a.btn-primary:has-text("Apply")','a:has-text("Apply")','button:has-text("Apply")']:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible(): el.click(); page.wait_for_load_state("networkidle",timeout=8000); break
            except: continue
        _gh_fill(page,'input[id="firstName"],input[name="firstName"]',applicant.get('first_name',''))
        _gh_fill(page,'input[id="lastName"],input[name="lastName"]',applicant.get('last_name',''))
        _gh_fill(page,'input[id="email"],input[name="email"],input[type="email"]',applicant.get('email',''))
        _gh_fill(page,'input[id="phone"],input[name="phone"],input[type="tel"]',applicant.get('phone',''))
        _gh_fill(page,'input[id="address"],input[name*="location" i]',applicant.get('location',''))
        _gh_fill(page,'input[name*="linkedin" i]',applicant.get('linkedin_url',''))
        if cv_path:
            for sel in ['input[type="file"]','input[name="resumeFile"]','input[name*="resume" i]']:
                try: page.set_input_files(sel,cv_path); print("[apply-engine] BambooHR: uploaded CV"); break
                except: continue
        bt,bd = _detect_blocker(page)
        if bt: result.update(status="manual_required",apply_failure_type=bt,apply_failure_detail=bd,error=f"BambooHR blocker: {bd}"); return result
        for sel in ['button[type="submit"]','input[type="submit"]','button:has-text("Submit")','button:has-text("Apply")']:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible(): el.click(); page.wait_for_load_state("networkidle",timeout=10000); break
            except: continue
        c = page.evaluate("document.body.innerText") or ""
        if any(w in c.lower() for w in ["thank","received","submitted","application"]):
            result.update(success=True,status="confirmed",confirmation_text=c[:500])
        else:
            result.update(success=True,status="submitted",confirmation_text=c[:500])
        return result
    except Exception as e:
        result["error"] = f"BambooHR handler error: {e}"; return result


def _apply_icims(page, applicant: dict, cv_path) -> dict:
    result = {"success": False, "status": "failed", "confirmation_text": "", "error": ""}
    try:
        for sel in ['.iCIMS_PrimaryApply a','a.iCIMS_PrimaryApply',
                    'a:has-text("Apply Now")','button:has-text("Apply Now")']:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible(): el.click(); page.wait_for_load_state("networkidle",timeout=8000); break
            except: continue
        for _step in range(8):
            try: page.wait_for_load_state("networkidle",timeout=6000)
            except: pass
            page_text = page.evaluate("document.body.innerText") or ""
            if any(p in page_text.lower() for p in _CONFIRMATION_PHRASES):
                result.update(success=True,status="confirmed",confirmation_text=page_text[:500]); return result
            bt,bd = _detect_blocker(page)
            if bt: result.update(status="manual_required",apply_failure_type=bt,apply_failure_detail=bd,error=f"iCIMS blocker: {bd}"); return result
            _gh_fill(page,'input[name*="firstname" i],input[id*="firstname" i]',applicant.get('first_name',''))
            _gh_fill(page,'input[name*="lastname" i],input[id*="lastname" i]',applicant.get('last_name',''))
            _gh_fill(page,'input[type="email"]',applicant.get('email',''))
            _gh_fill(page,'input[type="tel"]',applicant.get('phone',''))
            _gh_fill(page,'input[name*="city" i],input[name*="location" i]',applicant.get('location',''))
            if cv_path:
                for sel in ['input[type="file"]','input[name*="resume" i]']:
                    try: page.set_input_files(sel,cv_path); print("[apply-engine] iCIMS: uploaded CV"); break
                    except: continue
            submitted = False
            for sel in ['input[value*="Submit" i]','button:has-text("Submit")','a:has-text("Submit")','input[type="submit"]']:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible(): el.click(); submitted=True; break
                except: continue
            if submitted:
                try: page.wait_for_load_state("networkidle",timeout=10000)
                except: pass
                c = page.evaluate("document.body.innerText") or ""
                if any(p in c.lower() for p in _CONFIRMATION_PHRASES):
                    result.update(success=True,status="confirmed",confirmation_text=c[:500])
                else:
                    result.update(success=True,status="submitted",confirmation_text=c[:500])
                return result
            advanced = False
            for sel in ['input[value*="Next" i]','button:has-text("Next")','a:has-text("Next")','button:has-text("Continue")']:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible(): el.click(); advanced=True; break
                except: continue
            if not advanced: break
        result["error"] = "iCIMS: could not complete wizard after 8 steps"; return result
    except Exception as e:
        result["error"] = f"iCIMS handler error: {e}"; return result


def _apply_comeet(page, applicant: dict, cv_path) -> dict:
    result = {"success": False, "status": "failed", "confirmation_text": "", "error": ""}
    try:
        for sel in ['a.apply-btn','button.apply-btn','a:has-text("Apply")','button:has-text("Apply")']:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible(): el.click(); page.wait_for_load_state("networkidle",timeout=8000); break
            except: continue
        _gh_fill(page,'input[name*="first" i],input[placeholder*="First" i]',applicant.get('first_name',''))
        _gh_fill(page,'input[name*="last" i],input[placeholder*="Last" i]',applicant.get('last_name',''))
        _gh_fill(page,'input[type="email"]',applicant.get('email',''))
        _gh_fill(page,'input[type="tel"]',applicant.get('phone',''))
        _gh_fill(page,'input[name*="linkedin" i],input[placeholder*="LinkedIn" i]',applicant.get('linkedin_url',''))
        _gh_fill(page,'input[name*="location" i],input[name*="city" i]',applicant.get('location',''))
        if cv_path:
            for sel in ['input[type="file"]','input[name*="resume" i]','input[name*="cv" i]']:
                try: page.set_input_files(sel,cv_path); print("[apply-engine] Comeet: uploaded CV"); break
                except: continue
        bt,bd = _detect_blocker(page)
        if bt: result.update(status="manual_required",apply_failure_type=bt,apply_failure_detail=bd,error=f"Comeet blocker: {bd}"); return result
        for sel in ['button[type="submit"]','button:has-text("Submit")','button:has-text("Send Application")','input[type="submit"]']:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible(): el.click(); page.wait_for_load_state("networkidle",timeout=10000); break
            except: continue
        c = page.evaluate("document.body.innerText") or ""
        if any(w in c.lower() for w in ["thank","received","submitted","application"]):
            result.update(success=True,status="confirmed",confirmation_text=c[:500])
        else:
            result.update(success=True,status="submitted",confirmation_text=c[:500])
        return result
    except Exception as e:
        result["error"] = f"Comeet handler error: {e}"; return result


def _apply_jobvite(page, applicant: dict, cv_path) -> dict:
    result = {"success": False, "status": "failed", "confirmation_text": "", "error": ""}
    try:
        for sel in ['a.jv-btn-apply','a:has-text("Apply Now")','button:has-text("Apply Now")','a:has-text("Apply")']:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible(): el.click(); page.wait_for_load_state("networkidle",timeout=8000); break
            except: continue
        _gh_fill(page,'input[id="firstName"],input[name="firstName"]',applicant.get('first_name',''))
        _gh_fill(page,'input[id="lastName"],input[name="lastName"]',applicant.get('last_name',''))
        _gh_fill(page,'input[id="email"],input[type="email"]',applicant.get('email',''))
        _gh_fill(page,'input[id="phone"],input[type="tel"]',applicant.get('phone',''))
        _gh_fill(page,'input[name*="location" i],input[name*="city" i]',applicant.get('location',''))
        _gh_fill(page,'input[name*="linkedin" i],input[id*="linkedin" i]',applicant.get('linkedin_url',''))
        if cv_path:
            for sel in ['input[type="file"]','input[name*="resume" i]','input[id*="resume" i]']:
                try: page.set_input_files(sel,cv_path); print("[apply-engine] Jobvite: uploaded CV"); break
                except: continue
        bt,bd = _detect_blocker(page)
        if bt: result.update(status="manual_required",apply_failure_type=bt,apply_failure_detail=bd,error=f"Jobvite blocker: {bd}"); return result
        for sel in ['button[type="submit"]','input[type="submit"]','button:has-text("Submit Application")','button:has-text("Submit")']:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible(): el.click(); page.wait_for_load_state("networkidle",timeout=10000); break
            except: continue
        c = page.evaluate("document.body.innerText") or ""
        if any(w in c.lower() for w in ["thank","received","submitted","application"]):
            result.update(success=True,status="confirmed",confirmation_text=c[:500])
        else:
            result.update(success=True,status="submitted",confirmation_text=c[:500])
        return result
    except Exception as e:
        result["error"] = f"Jobvite handler error: {e}"; return result


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
# Gemini is the primary scorer in the rest of the project; the apply engine
# now uses it as the JSON-output AI too, so removing ANTHROPIC_API_KEY no
# longer breaks the single-shot 'analyze form HTML' strategy. The tool-use
# agent strategy still requires Anthropic (uses Claude's tools API which is
# vendor-specific); without it the engine cleanly degrades to manual_required.
GEMINI_KEY = (
    os.environ.get("GEMINI_API_KEY")
    or os.environ.get("GEMINI_KEY")
    or os.environ.get("GOOGLE_API_KEY", "")
)


import random as _random

def _human_delay(lo: float = 0.05, hi: float = 0.25):
    """Sleep for a random human-like interval between actions."""
    time.sleep(_random.uniform(lo, hi))


def _browser_profile_dir(user_id: int) -> str:
    """Return (and create) a per-user browser profile directory for cookie persistence."""
    base = os.environ.get("BROWSER_PROFILES_DIR",
                          os.path.join(os.environ.get("EVIDENCE_BASE_DIR", "/data/evidence"),
                                       "..", "browser-profiles"))
    d = os.path.join(base, str(user_id) if user_id else "default")
    os.makedirs(d, exist_ok=True)
    return d


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
    """Call Gemini and return the text response.

    Name kept for compatibility — was an Anthropic call, now routes through
    Gemini 2.5 Flash (Gemini-only refactor, 2026-05-27).
    """
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": max(max_tokens * 2, 2048),
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }).encode()
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "gemini-2.5-flash:generateContent?key=" + GEMINI_KEY)
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned 0 candidates: {json.dumps(data.get('promptFeedback', {}))[:200]}")
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    text = (parts[0].get("text", "") if parts else "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    return text.strip()

def _gemini_json(prompt: str, max_tokens: int = 2048):
    """Call Gemini 2.5 Flash with JSON mode + thinking disabled, parse response.

    Used by _claude_json as primary so the apply engine works without an
    Anthropic key. Mirrors the patterns from app.py's search scorer.
    """
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            # 2x because thinkingBudget=0 disables the reasoning portion but we
            # still want headroom for verbose form-instruction output
            "maxOutputTokens": max(max_tokens * 2, 4096),
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }).encode()
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "gemini-2.5-flash:generateContent?key=" + GEMINI_KEY)
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    # Defensive extraction — Gemini can SAFETY/RECITATION-block silently
    candidates = data.get("candidates") or []
    if not candidates:
        pf = data.get("promptFeedback", {})
        raise ValueError(f"Gemini returned 0 candidates (promptFeedback={json.dumps(pf)[:200]})")
    cand = candidates[0]
    finish = cand.get("finishReason", "")
    content = cand.get("content") or {}
    parts = content.get("parts") or []
    text = (parts[0].get("text", "") if parts else "").strip()
    if not text:
        raise ValueError(f"Gemini returned empty text (finishReason={finish!r})")
    # With responseMimeType=application/json the text should already be parseable
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            return json.loads(m.group())
        m = re.search(r'\[.*\]', text, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise ValueError(f"No JSON in Gemini response: {text[:200]}")


def _claude_json(prompt: str, max_tokens: int = 1024):
    """Call Gemini and parse JSON. Name kept for caller-compat.

    Was Anthropic + Gemini fallback. Per Gemini-only refactor (2026-05-27)
    this dispatches solely to Gemini.
    """
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY not set in Railway environment")
    return _gemini_json(prompt, max_tokens)

# ── Applicant data extraction ─────────────────────────────────────────────────

def extract_applicant_data(cv_text: str, email: str) -> dict:
    """Extract structured applicant data from CV text using Claude."""
    empty = {
        "email": email, "full_name": "", "first_name": "", "last_name": "",
        "phone": "", "linkedin_url": "", "location": "", "current_title": "",
        "current_company": "", "years_experience": 0, "summary": "",
    }
    # _claude_json routes through Gemini only — require GEMINI_KEY
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


def _exec_interactions(page, instructions: list, cv_path) -> bool:
    """Execute a list of form-interaction dicts. Returns True if submit was hit."""
    submitted = False
    for instr in instructions:
        action = instr.get("action", "")
        sel    = instr.get("selector", "")
        _human_delay(0.06, 0.22)
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
                time.sleep(0.3)
        except Exception as e:
            print(f"[apply-engine] instr {instr}: {e}")
    return submitted


def _apply_generic_single_shot(page, applicant: dict, cv_path,
                                job_title: str, company: str,
                                page_html: str) -> dict:
    """Attempt 1: send first 8 KB of HTML to Claude, execute returned interactions."""
    result = {"success": False, "status": "failed", "confirmation_text": "",
              "error": "", "submitted": False}
    try:
        prompt = (
            f'Fill job application for "{job_title}" at "{company}".\n\n'
            f'Applicant data:\n{json.dumps(applicant, indent=2)}\n\n'
            f'Page HTML (first 8000 chars):\n{page_html[:8000]}\n\n'
            'Return a JSON ARRAY of interactions (no explanation):\n'
            ' {"action":"fill","selector":"CSS","value":"text"}\n'
            ' {"action":"select","selector":"CSS","value":"option value or label"}\n'
            ' {"action":"upload","selector":"CSS","file":"cv"}\n'
            ' {"action":"check","selector":"CSS"}\n'
            ' {"action":"click","selector":"CSS"}\n'
            ' {"action":"submit","selector":"CSS of submit button"}\n\n'
            'Rules: only visible/required fields; end with submit.\n'
            'If impossible (login/captcha/no form), return {"error":"reason"}'
        )
        instructions = _claude_json(prompt, max_tokens=2048)
    except Exception as e:
        result.update(error=f"Form analysis failed: {e}", status="manual_required")
        return result

    if isinstance(instructions, dict) and "error" in instructions:
        result.update(error=instructions["error"], status="manual_required")
        return result
    if not isinstance(instructions, list) or not instructions:
        result.update(error="No form interactions generated", status="manual_required")
        return result

    bt, bd = _detect_blocker(page)
    if bt:
        result.update(status="manual_required", apply_failure_type=bt,
                      apply_failure_detail=bd, error=f"Blocked before submit: {bd}")
        return result

    submitted = _exec_interactions(page, instructions, cv_path)
    if not submitted:
        for s in ['button[type="submit"]', 'input[type="submit"]',
                  'button:has-text("Submit")', 'button:has-text("Apply")']:
            try:
                page.click(s, timeout=3_000); submitted = True; break
            except Exception:
                continue
    result["submitted"] = submitted
    if not submitted:
        result["error"] = "Could not find or click submit button"
    return result


def _apply_generic_chunked(page, applicant: dict, cv_path,
                            job_title: str, company: str) -> dict:
    """Attempt 2: chunk full HTML into 6 KB slices, gather all field interactions,
    deduplicate by selector, then execute. Catches fields beyond first 8 KB."""
    CHUNK = 6000
    result = {"success": False, "status": "failed", "confirmation_text": "",
              "error": "", "submitted": False}
    try:
        full_html = page.content()
        chunks = [full_html[i:i+CHUNK] for i in range(0, min(len(full_html), 90_000), CHUNK)]
        print(f"[apply-engine] Chunked: {len(chunks)} chunks from {len(full_html)} chars")

        seen: set = set()
        all_interactions: list = []

        for idx, chunk in enumerate(chunks):
            try:
                prompt = (
                    f'Extract form interactions from HTML chunk {idx+1}/{len(chunks)} '
                    f'for a job application for "{job_title}" at "{company}".\n'
                    f'Applicant: {json.dumps({k:v for k,v in applicant.items() if v})}\n'
                    f'HTML chunk:\n{chunk}\n\n'
                    'Return a JSON ARRAY of NEW interactions.\n'
                    ' {"action":"fill","selector":"CSS","value":"text"}\n'
                    ' {"action":"select","selector":"CSS","value":"option"}\n'
                    ' {"action":"upload","selector":"CSS","file":"cv"}\n'
                    ' {"action":"submit","selector":"CSS"}\n'
                    'Return [] if no new fields. No explanation.'
                )
                chunk_result = _claude_json(prompt, max_tokens=1024)
            except Exception as e:
                print(f"[apply-engine] Chunk {idx+1} error: {e}")
                continue
            if not isinstance(chunk_result, list):
                continue
            for instr in chunk_result:
                sel = instr.get("selector", "")
                if sel and sel not in seen:
                    seen.add(sel)
                    all_interactions.append(instr)

        if not all_interactions:
            result.update(error="Chunked: no interactions found across all chunks",
                          status="manual_required")
            return result

        submits = [i for i in all_interactions if i.get("action") == "submit"]
        non_sub = [i for i in all_interactions if i.get("action") != "submit"]
        ordered = non_sub + submits
        print(f"[apply-engine] Chunked: {len(ordered)} interactions ({len(submits)} submit)")

        bt, bd = _detect_blocker(page)
        if bt:
            result.update(status="manual_required", apply_failure_type=bt,
                          apply_failure_detail=bd, error=f"Chunked blocked: {bd}")
            return result

        submitted = _exec_interactions(page, ordered, cv_path)
        if not submitted:
            for s in ['button[type="submit"]', 'input[type="submit"]',
                      'button:has-text("Submit")', 'button:has-text("Apply")']:
                try:
                    page.click(s, timeout=3_000); submitted = True; break
                except Exception:
                    continue
        result["submitted"] = submitted
        if not submitted:
            result["error"] = "Chunked: could not find submit button"
        return result
    except Exception as e:
        result.update(error=f"Chunked strategy error: {e}")
        return result


def _apply_generic_agent(page, applicant: dict, cv_path,
                          job_title: str, company: str) -> dict:
    """Attempt 3: Gemini-driven 'last-chance' retry of the chunked strategy
    with a more aggressive prompt that includes the full visible form HTML.

    Was a Claude tool-use agent — replaced with a Gemini single-shot per
    Gemini-only refactor (2026-05-27). The Claude tools API isn't available
    on Gemini, so when both prior attempts (single-shot + chunked) failed,
    we send the FULL page HTML (up to 30 KB) to Gemini one more time with
    instructions emphasising it's the last automated chance before manual
    fallback. If even this fails, the job goes to manual_required cleanly.
    """
    result = {"success": False, "status": "failed", "confirmation_text": "",
              "error": "", "submitted": False}

    if not GEMINI_KEY:
        result.update(error="No GEMINI_API_KEY for last-chance retry",
                      status="manual_required")
        return result

    # Gemini-driven "last chance" — single shot with the FULL form HTML
    # (~30KB), and a more aggressive prompt than the prior attempts.
    try:
        full_html = page.content()[:30000]
        page_text = (page.evaluate("document.body.innerText") or "")[:2000]
        prompt = (
            f'LAST AUTOMATED ATTEMPT to fill job application for "{job_title}" at "{company}".\n'
            f'Prior attempts (single-shot + chunked) both failed.\n\n'
            f'Applicant data:\n{json.dumps(applicant, indent=2)}\n\n'
            f'Page text (first 2000 chars):\n{page_text}\n\n'
            f'Full HTML (up to 30000 chars):\n{full_html}\n\n'
            'Return a JSON ARRAY of interactions (no explanation):\n'
            ' {"action":"fill","selector":"CSS","value":"text"}\n'
            ' {"action":"select","selector":"CSS","value":"option value or label"}\n'
            ' {"action":"upload","selector":"CSS","file":"cv"}\n'
            ' {"action":"check","selector":"CSS"}\n'
            ' {"action":"click","selector":"CSS"}\n'
            ' {"action":"submit","selector":"CSS of submit button"}\n\n'
            'Rules:\n'
            '- Fill EVERY required field you can identify\n'
            '- Use precise CSS selectors (prefer id over class, class over tag)\n'
            '- End with a submit action\n'
            '- If page has CAPTCHA / login wall / human-only step, return {"error":"reason"}\n'
        )
        instructions = _claude_json(prompt, max_tokens=4096)
    except Exception as e:
        result.update(error=f"Last-chance Gemini call failed: {e}",
                      status="manual_required")
        return result

    if isinstance(instructions, dict) and instructions.get("error"):
        result.update(error=f"Page not automatable: {instructions['error']}",
                      status="manual_required")
        return result

    if not isinstance(instructions, list):
        result.update(error="Gemini did not return interactions list",
                      status="manual_required")
        return result

    submitted = _exec_interactions(page, instructions, cv_path)
    if submitted:
        try:
            page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass
        confirmation = page.evaluate("document.body.innerText") or ""
        if any(p in confirmation.lower() for p in _CONFIRMATION_PHRASES):
            result.update(success=True, status="confirmed",
                          confirmation_text=confirmation[:500], submitted=True)
        else:
            result.update(success=True, status="submitted",
                          confirmation_text=confirmation[:500], submitted=True)
        return result

    result.update(error="Gemini returned instructions but submit was never reached",
                  status="manual_required")
    return result



def submit_application(
    job_url: str,
    job_title: str,
    company: str,
    applicant: dict,
    cv_path: str | None,
    api_key: str = "",
    *,
    user_id: int = 0,
    job_id: int = 0,
    attempt: int = 1,
) -> dict:
    """
    Submit a job application using headless Chromium + Claude.

    If job_url is from a job board (LinkedIn, Indeed, etc.), automatically
    searches for and navigates to the company's own career page instead.

    Args:
        user_id / job_id / attempt: used for evidence path (screenshots).

    Returns:
        {
            "success": bool,
            "status": "confirmed" | "submitted" | "failed" | "manual_required",
            "confirmation_text": str,
            "error": str,
            "resolved_url": str,   # actual URL used (may differ from job_url)
            "evidence_path": str,  # directory containing screenshots
        }
    """
    # api_key is now the Gemini key (Gemini-only refactor 2026-05-27).
    # Override the module-level GEMINI_KEY if a key was passed explicitly.
    global GEMINI_KEY
    if api_key:
        GEMINI_KEY = api_key

    _base = {
        "success": False, "status": "failed", "confirmation_text": "", "error": "",
        "apply_failure_type": None, "apply_failure_detail": None, "resolved_url": job_url,
        "evidence_path": _evidence_path_str(user_id, job_id, attempt),
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
            profile_dir = _browser_profile_dir(user_id)
            print(f"[apply-engine] Launching Chromium (profile: {profile_dir})…")
            # Use a persistent context so cookies/localStorage carry over between runs,
            # making the browser fingerprint look like a returning human user.
            ctx = pw.chromium.launch_persistent_context(
                profile_dir,
                headless=True,
                user_agent=_UA,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"],
                viewport={"width": 1280, "height": 800},
                locale="en-US",
                timezone_id="America/New_York",
            )
            browser = ctx  # alias so browser.close() still works
            page = ctx.new_page()
            if _STEALTH_AVAILABLE:
                _stealth_sync(page)
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

            # 2. Check for CAPTCHA / login walls ───────────────────────────────
            blocker_type, blocker_detail = _detect_blocker(page)
            if blocker_type:
                _screenshot(page, user_id, job_id, attempt, "blocker")
                _dump_html(page, user_id, job_id, attempt, "blocker")
                browser.close()
                return {**_base, "status": "manual_required",
                        "apply_failure_type": blocker_type,
                        "apply_failure_detail": blocker_detail,
                        "error": f"Blocked: {blocker_detail}"}

            # Legacy phrase check (belt-and-suspenders)
            if any(p in lower for p in _BLOCKER_PHRASES):
                _screenshot(page, user_id, job_id, attempt, "login_wall")
                browser.close()
                return {**_base, "status": "manual_required",
                        "apply_failure_type": "login_wall",
                        "apply_failure_detail": "Login phrase matched in page text",
                        "error": "Login or account creation required to apply"}

            # 2b. ATS-specific fast path ──────────────────────────────────────
            if _is_greenhouse(actual_url):
                print(f"[apply-engine] Detected Greenhouse ATS")
                _screenshot(page, user_id, job_id, attempt, "pre_submit")
                res = _apply_greenhouse(page, applicant, cv_path)
                _screenshot(page, user_id, job_id, attempt, "post_submit")
                browser.close()
                res["resolved_url"] = actual_url
                res["evidence_path"] = _evidence_path_str(user_id, job_id, attempt)
                return _add_failure_type(res)

            if _is_lever(actual_url):
                print(f"[apply-engine] Detected Lever ATS")
                _screenshot(page, user_id, job_id, attempt, "pre_submit")
                res = _apply_lever(page, applicant, cv_path)
                _screenshot(page, user_id, job_id, attempt, "post_submit")
                browser.close()
                res["resolved_url"] = actual_url
                res["evidence_path"] = _evidence_path_str(user_id, job_id, attempt)
                return _add_failure_type(res)

            if _is_workday(actual_url):
                print(f"[apply-engine] Detected Workday ATS")
                _screenshot(page, user_id, job_id, attempt, "pre_submit")
                res = _apply_workday(page, applicant, cv_path)
                _screenshot(page, user_id, job_id, attempt, "post_submit")
                browser.close()
                res["resolved_url"] = actual_url
                res["evidence_path"] = _evidence_path_str(user_id, job_id, attempt)
                return _add_failure_type(res)

            if _is_ashby(actual_url):
                print(f"[apply-engine] Detected Ashby ATS")
                _screenshot(page, user_id, job_id, attempt, "pre_submit")
                res = _apply_ashby(page, applicant, cv_path)
                _screenshot(page, user_id, job_id, attempt, "post_submit")
                browser.close()
                res["resolved_url"] = actual_url
                res["evidence_path"] = _evidence_path_str(user_id, job_id, attempt)
                return _add_failure_type(res)

            if _is_smartrecruiters(actual_url):
                print(f"[apply-engine] Detected SmartRecruiters ATS")
                _screenshot(page, user_id, job_id, attempt, "pre_submit")
                res = _apply_smartrecruiters(page, applicant, cv_path)
                _screenshot(page, user_id, job_id, attempt, "post_submit")
                browser.close()
                res["resolved_url"] = actual_url
                res["evidence_path"] = _evidence_path_str(user_id, job_id, attempt)
                return _add_failure_type(res)

            if _is_bamboohr(actual_url):
                print(f"[apply-engine] Detected BambooHR ATS")
                _screenshot(page, user_id, job_id, attempt, "pre_submit")
                res = _apply_bamboohr(page, applicant, cv_path)
                _screenshot(page, user_id, job_id, attempt, "post_submit")
                browser.close()
                res["resolved_url"] = actual_url
                res["evidence_path"] = _evidence_path_str(user_id, job_id, attempt)
                return _add_failure_type(res)

            if _is_icims(actual_url):
                print(f"[apply-engine] Detected iCIMS ATS")
                _screenshot(page, user_id, job_id, attempt, "pre_submit")
                res = _apply_icims(page, applicant, cv_path)
                _screenshot(page, user_id, job_id, attempt, "post_submit")
                browser.close()
                res["resolved_url"] = actual_url
                res["evidence_path"] = _evidence_path_str(user_id, job_id, attempt)
                return _add_failure_type(res)

            if _is_comeet(actual_url):
                print(f"[apply-engine] Detected Comeet ATS")
                _screenshot(page, user_id, job_id, attempt, "pre_submit")
                res = _apply_comeet(page, applicant, cv_path)
                _screenshot(page, user_id, job_id, attempt, "post_submit")
                browser.close()
                res["resolved_url"] = actual_url
                res["evidence_path"] = _evidence_path_str(user_id, job_id, attempt)
                return _add_failure_type(res)

            if _is_jobvite(actual_url):
                print(f"[apply-engine] Detected Jobvite ATS")
                _screenshot(page, user_id, job_id, attempt, "pre_submit")
                res = _apply_jobvite(page, applicant, cv_path)
                _screenshot(page, user_id, job_id, attempt, "post_submit")
                browser.close()
                res["resolved_url"] = actual_url
                res["evidence_path"] = _evidence_path_str(user_id, job_id, attempt)
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

            # 4. Strategy dispatch based on attempt number ─────────────────────
            #    attempt 1 → single-shot (first 8 KB)
            #    attempt 2 → chunked HTML (full page, 6 KB slices)
            #    attempt 3 → LLM browser agent (tool-use loop, max 25 turns)
            _screenshot(page, user_id, job_id, attempt, "pre_submit")
            strategy_name = {1: "single-shot", 2: "chunked", 3: "agent"}.get(attempt, "single-shot")
            print(f"[apply-engine] Generic strategy: {strategy_name} (attempt {attempt})")

            if attempt == 2:
                strat_res = _apply_generic_chunked(page, applicant, cv_path,
                                                   job_title, company)
            elif attempt >= 3:
                strat_res = _apply_generic_agent(page, applicant, cv_path,
                                                  job_title, company)
            else:
                strat_res = _apply_generic_single_shot(page, applicant, cv_path,
                                                        job_title, company, page_html)

            if not strat_res.get("submitted"):
                browser.close()
                err = strat_res.get("error", "Strategy failed")
                ft  = strat_res.get("apply_failure_type") or _classify_failure(err)[0]
                fd  = strat_res.get("apply_failure_detail") or _classify_failure(err)[1]
                status = strat_res.get("status", "failed")
                return {**_base, "status": status, "error": err,
                        "apply_failure_type": ft, "apply_failure_detail": fd}

            # If agent already confirmed (submit_form returned early), propagate
            if strat_res.get("success") and strat_res.get("status") in ("confirmed","submitted"):
                browser.close()
                return {
                    "success": strat_res["success"],
                    "status":  strat_res["status"],
                    "confirmation_text": strat_res.get("confirmation_text",""),
                    "error": "",
                    "resolved_url": actual_url,
                    "evidence_path": _evidence_path_str(user_id, job_id, attempt),
                    "apply_failure_type": None,
                    "apply_failure_detail": None,
                }

            # 5. Wait for result page ──────────────────────────────────────────
            try:
                page.wait_for_load_state("networkidle", timeout=12_000)
            except PWTimeout:
                pass

            result_text = page.evaluate("document.body.innerText") or ""
            result_url  = page.url
            _screenshot(page, user_id, job_id, attempt, "post_submit")

            # 6. Verify confirmation ───────────────────────────────────────────
            phrase_ok = any(p in result_text.lower() for p in _CONFIRMATION_PHRASES)
            try:
                v = _claude_json(
                    f'After submitting application for "{job_title}" at "{company}":\n'
                    f'URL: {result_url}\nPage: {result_text[:2000]}\n\n'
                    f'Was the application successfully received?\n'
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
                "evidence_path": _evidence_path_str(user_id, job_id, attempt),
                "apply_failure_type": None,
                "apply_failure_detail": None,
            }

    except Exception as e:
        err = str(e)
        ft, fd = _classify_failure(err)
        status = "manual_required" if ft in ("captcha", "login_wall") else "failed"
        try:
            _screenshot(page, user_id, job_id, attempt, "error")
            _dump_html(page, user_id, job_id, attempt, "error")
        except Exception:
            pass
        return {**_base, "status": status, "error": err,
                "apply_failure_type": ft, "apply_failure_detail": fd,
                "evidence_path": _evidence_path_str(user_id, job_id, attempt)}
