#!/usr/bin/env python3
"""End-to-end browser test of the new-user funnel, for STAGING (launch item C12).

    python3 scripts/e2e.py --base https://web-staging-8e79.up.railway.app \
        --key <JH_LOADTEST_KEY> [--persona product,qa] [--search-minutes 12] [--out e2e-out]

For each persona in tests/fixtures/personas (all eight by default), in a real
Chromium at phone size:
  1. landing page loads and its "Meet Noa" button leads to sign-up
  2. sign up (name, email on the test domain, password)
  3. onboarding opens ("Meet Noa"); upload the persona's CV
  4. the CV is read: "Next" appears and the job titles come pre-filled
  5. "Start my search"
  6. picks arrive: an "Approve" button appears within --search-minutes
  7. approve one pick, pass one; the approved job is in /api/jobs?status=approved
Every step is timed and screenshotted; a JSON report is written. Exit 1 if any
persona fails a step. Staging should run JH_FAKE_AI=1 so step 6 costs nothing.

It refuses production. Day-1 checks on production are E11, by hand.
"""
import argparse
import json
import os
import pathlib
import random
import string
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
PERSONAS = ROOT / "tests" / "fixtures" / "personas"
PROD_HOSTS = ("job-hunter.work", "web-production-192b7.up.railway.app")
PASSWORD = "e2e-Pw-2026!"


class StepFailed(Exception):
    pass


def run_persona(browser, base, key, p, out, search_minutes, run_id):
    steps, t0 = [], time.time()
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                              extra_http_headers={"X-Loadtest-Key": key} if key else {})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)[:200]))
    email = "e2e-%s-%s@job-hunter.test" % (run_id, p["key"])

    def step(name, fn):
        s = time.time()
        try:
            fn()
            ok, err = True, None
        except Exception as e:                       # one failed step ends this persona
            ok, err = False, "%s: %s" % (type(e).__name__, str(e)[:300])
        shot = out / ("%s-%02d-%s.png" % (p["key"], len(steps) + 1, name.replace(" ", "-")))
        try:
            page.screenshot(path=str(shot), full_page=False)
        except Exception as e:
            err = (err or "") + " (screenshot failed: %s)" % e
        steps.append({"step": name, "ok": ok, "seconds": round(time.time() - s, 1),
                      "error": err, "screenshot": shot.name})
        print("  %-4s %-28s %6.1fs %s" % ("ok" if ok else "FAIL", name, time.time() - s, err or ""))
        if not ok:
            raise StepFailed(name)

    try:
        def landing():
            page.goto(base + "/", wait_until="domcontentloaded")
            page.get_by_text("Meet Noa").first.wait_for(timeout=15000)
        step("landing", landing)

        def signup():
            page.goto(base + "/register", wait_until="domcontentloaded")
            page.fill("#name", p["name"])
            page.fill("#email", email)
            page.fill("#password", PASSWORD)
            page.fill("#password2", PASSWORD)
            page.locator("form button[type=submit], form input[type=submit]").first.click()
            page.wait_for_url("**/app**", timeout=20000)
        step("sign up", signup)

        def onboarding_open():
            page.get_by_text("Meet Noa").first.wait_for(timeout=20000)
        step("onboarding opens", onboarding_open)

        def upload():
            page.set_input_files("input[type=file]", str(PERSONAS / p["cv"]))
            # When the CV is read, onboarding moves on by itself to "What to look
            # for"; on a slow read it may instead offer an enabled "Next".
            deadline = time.time() + 90
            while time.time() < deadline:
                if page.get_by_role("button", name="Start my search").count():
                    return
                nxt = page.get_by_role("button", name="Next")
                if nxt.count() and nxt.first.is_enabled():
                    nxt.first.click()
                    return
                page.wait_for_timeout(500)
            raise AssertionError("the CV was not read within 90 s")
        step("CV upload and read", upload)

        def titles():
            page.get_by_role("button", name="Start my search").wait_for(timeout=15000)
            chips = page.locator("text=Job titles").first
            chips.wait_for(timeout=5000)
            if page.get_by_role("button", name="Start my search").is_disabled():
                raise AssertionError("no job titles were filled in from the CV")
        step("titles pre-filled", titles)

        def start():
            page.get_by_role("button", name="Start my search").click()
            page.wait_for_timeout(3000)
        step("start search", start)

        def picks():
            deadline = time.time() + search_minutes * 60
            while time.time() < deadline:
                if page.get_by_role("button", name="Approve").count():
                    return
                page.wait_for_timeout(20000)
                page.reload(wait_until="domcontentloaded")
            raise AssertionError("no picks after %s minutes" % search_minutes)
        step("picks arrive", picks)

        def decide():
            page.evaluate("window.scrollTo(0, document.scrollingElement.scrollHeight)")
            page.wait_for_timeout(400)
            page.get_by_role("button", name="Approve").first.click(timeout=10000)
            page.wait_for_timeout(1500)
            if page.get_by_role("button", name="Pass").count():
                page.get_by_role("button", name="Pass").first.click()
            r = page.request.get(base + "/api/jobs?status=approved")
            if r.status != 200:
                raise AssertionError("/api/jobs answered %s" % r.status)
            data = r.json()
            jobs = data if isinstance(data, list) else data.get("jobs", [])
            if not jobs:
                raise AssertionError("the approved job is not in the approved list")
        step("approve and pass", decide)
        result = "pass"
    except StepFailed:
        result = "fail"
    finally:
        ctx.close()
    return {"persona": p["key"], "family": p["family"], "email": email, "result": result,
            "seconds": round(time.time() - t0), "steps": steps, "page_errors": errors[:10]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--key", default="", help="staging JH_LOADTEST_KEY (skips the per-IP sign-up limit)")
    ap.add_argument("--persona", default="", help="comma list of persona keys; default all")
    ap.add_argument("--search-minutes", type=float, default=12)
    ap.add_argument("--out", default="e2e-out")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()

    base = a.base.rstrip("/")
    if any(h in base for h in PROD_HOSTS):
        sys.exit("Refusing to run sign-ups against production (%s). Use staging." % base)
    meta = json.load(open(PERSONAS / "personas.json"))
    if a.persona:
        want = set(a.persona.split(","))
        meta = [m for m in meta if m["key"] in want]
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    run_id = "".join(random.choice(string.ascii_lowercase) for _ in range(5))

    from playwright.sync_api import sync_playwright
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not a.headed)
        for p in meta:
            print("%s (%s)" % (p["key"], p["family"]))
            results.append(run_persona(browser, base, a.key, p, out, a.search_minutes, run_id))
        browser.close()

    passed = sum(r["result"] == "pass" for r in results)
    report = {"base": base, "run": run_id, "passed": passed, "of": len(results), "personas": results}
    (out / "e2e-report.json").write_text(json.dumps(report, indent=2))
    print("\nE2E: %d of %d personas passed. Report and screenshots in %s" % (passed, len(results), out))
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
