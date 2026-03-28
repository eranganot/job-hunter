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
import urllib.request
import urllib.error
from datetime import datetime

# ── Playwright ────────────────────────────────────────────────────────────────
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

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
    "application submitted", "application received", "thank you for applying",
    "thanks for applying", "successfully submitted", "application complete",
    "we received your application", "your application has been",
    "we'll be in touch", "we will be in touch",
]


# ── Claude helpers ────────────────────────────────────────────────────────────

def _claude(prompt: str, max_tokens: int = 1024) -> str:
    """Call Claude and return the text response."""
    payload = json.dumps({
        "model": "claude-haiku-4-5-20251001",
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
        "phone": "", "linkedin_url": "", "location": "",
        "current_title": "", "current_company": "",
        "years_experience": 0, "summary": "",
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

    Returns:
        {
          "success": bool,
          "status": "confirmed" | "submitted" | "failed" | "manual_required",
          "confirmation_text": str,
          "error": str,
        }
    """
    global ANTHROPIC_KEY
    if api_key:
        ANTHROPIC_KEY = api_key

    _base = {"success": False, "status": "failed", "confirmation_text": "", "error": ""}

    if not PLAYWRIGHT_AVAILABLE:
        return {**_base, "status": "manual_required",
                "error": "Playwright not installed — run: playwright install chromium --with-deps"}
    if not job_url:
        return {**_base, "error": "No job URL provided"}

    try:
        with sync_playwright() as pw:
            print(f"[apply-engine] Launching Chromium...")
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
                page.goto(job_url, wait_until="domcontentloaded", timeout=30_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except PWTimeout:
                    pass
            except PWTimeout:
                browser.close()
                return {**_base, "error": "Page load timed out"}

            page_text = page.evaluate("document.body.innerText") or ""
            lower = page_text.lower()

            # 2. Check for login walls ────────────────────────────────────────
            if any(p in lower for p in _BLOCKER_PHRASES):
                browser.close()
                return {**_base, "status": "manual_required",
                        "error": "Login or account creation required to apply"}

            # 3. Click Apply button if no form visible yet ────────────────────
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
                    f'  {{"action":"fill","selector":"CSS","value":"text"}}\n'
                    f'  {{"action":"select","selector":"CSS","value":"option value or label"}}\n'
                    f'  {{"action":"upload","selector":"CSS","file":"cv"}}\n'
                    f'  {{"action":"check","selector":"CSS"}}\n'
                    f'  {{"action":"click","selector":"CSS"}}\n'
                    f'  {{"action":"submit","selector":"CSS of submit button"}}\n\n'
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
                sel = instr.get("selector", "")
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
                return {**_base, "error": "Could not find or click submit button"}

            # 6. Wait for result page ─────────────────────────────────────────
            try:
                page.wait_for_load_state("networkidle", timeout=12_000)
            except PWTimeout:
                pass
            result_text = page.evaluate("document.body.innerText") or ""
            result_url = page.url

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
            }

    except Exception as e:
        err = str(e)
        status = "manual_required" if any(
            x in err.lower() for x in ("login", "captcha", "account")
        ) else "failed"
        return {**_base, "status": status, "error": err}
