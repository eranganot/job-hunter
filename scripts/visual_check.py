#!/usr/bin/env python3
"""
scripts/visual_check.py - screenshot /app at the three widths Phase 4 names.

Phase 4 item 6: "Playwright screenshots at 390 / 768 / 1280 px". The point is
not to look at pictures - it is that a layout can break at one width while every
CSS assertion in the suite still passes, because the assertions check that a
class EXISTS, not that the result is usable.

Serves the committed web_bundle/ and stubs /api/* with fixtures, so it exercises
the artefact users actually receive rather than a dev server. No credentials, no
network, nothing written outside the output directory.

    python3 scripts/visual_check.py [--out DIR]

Needs Playwright with Chromium. It is not in requirements for the device; run it
where a browser is available. Exits non-zero if a page reports a console error
or renders nothing, so it is useful unattended as well as by eye.
"""
import argparse
import http.server
import json
import os
import pathlib
import socketserver
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "web_bundle"
WIDTHS = [("phone", 390, 844), ("tablet", 768, 1024), ("laptop", 1280, 800)]

# Enough shape for every screen to render with real content. Deliberately
# includes the awkward cases: a long title, a penalised job, a bulk-marked
# "applied" that never was.
def _job(i, **kw):
    j = {"id": i, "title": "Senior Product Manager, Onboarding and Growth Platform",
         "company": "Payoneer", "location": "Herzliya, Israel",
         "url": "https://example.test/j/%d" % i, "status": "new",
         "match_score": 77, "candidate_score": 74, "url_verified": 1,
         "feedback_penalty": 0, "feedback_reason": "",
         "why_relevant": "Product-led growth, retention and AI-powered onboarding line up "
                         "with the role's focus, and the location is a match.",
         "found_date": "2026-09-14T09:00:00", "apply_status": None, "stage": None,
         "applied_via": None, "description": "Own the onboarding funnel end to end."}
    j.update(kw); return j

FIXTURES = {
    "/api/me": {"id": 1, "name": "Eran", "email": "eran@example.test", "role": "admin",
                "plan": "free", "onboarding_complete": 1, "onboarding_dismissed": 1,
                "cv_filename": "cv.pdf", "job_titles": ["VP Product"], "keywords": ["growth"],
                "locations": ["Tel Aviv"], "schedule_frequency": "weekly", "search_hour": 11,
                "apply_hour": 17, "search_day_of_week": 1, "apply_day_of_week": 1,
                "notification_channel": "telegram,email", "auto_apply_enabled": 0},
    "/api/stats": {"new": 3, "approved": 1, "applied": 136, "deferred": 0, "rejected": 435,
                   "total": 605, "expired": 0,
                   "applied_engine": 8, "applied_manual": 42, "applied_bulk": 84,
                   "applied_no_url": 0, "applied_unknown": 2,
                   "passed_by_user": 200, "passed_by_system": 33, "passed_unknown": 0,
                   "rejected_archived": 202},
    "/api/activity": [],
    "/api/learned": {"pass_reasons": [], "blocklist": [], "patterns": []},
}

JOBS = {
    "new":      [_job(1), _job(2, match_score=80, feedback_penalty=10,
                              feedback_reason="Location you've passed on")],
    "approved": [_job(3, status="approved", apply_status="queued")],
    "applied":  [_job(4, status="applied", apply_status="submitted", applied_via="engine"),
                 _job(5, status="applied", apply_status="manual", applied_via="bulk")],
    "deferred": [_job(6, status="deferred")],
}


class _Handler(http.server.SimpleHTTPRequestHandler):
    """Serves web_bundle/ UNDER /app/, because that is where it lives in
    production: Vite is built with base=/app/, so index.html asks for
    /app/assets/*. Serving it at the root gives 404s on every asset and a blank
    page - which is what the first run of this script produced, and is exactly
    the failure it exists to catch, just aimed at the harness."""

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(BUNDLE), **kw)

    def translate_path(self, path):
        if path.startswith("/app/"):
            path = path[len("/app"):]
        elif path == "/app":
            path = "/index.html"
        return super().translate_path(path)

    def log_message(self, *a):
        pass


def serve():
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "_visual"))
    args = ap.parse_args()
    out = pathlib.Path(args.out); out.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed here. pip install playwright && playwright install chromium")
        return 2

    httpd, port = serve()
    base = "http://127.0.0.1:%d" % port
    problems, shots = [], []

    with sync_playwright() as pw:
        exe = os.environ.get("JH_CHROMIUM")      # /opt/pw-browsers/chromium in the sandbox
        browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        for name, w, h in WIDTHS:
            ctx = browser.new_context(viewport={"width": w, "height": h},
                                      device_scale_factor=2 if w == 390 else 1)
            page = ctx.new_page()
            errors = []
            page.on("console", lambda m: m.type == "error" and errors.append(m.text))
            page.on("pageerror", lambda e: errors.append(str(e)))

            def route(r):
                u = r.request.url
                path = u.split("127.0.0.1:%d" % port)[-1].split("?")[0]
                if path in FIXTURES:
                    return r.fulfill(status=200, content_type="application/json",
                                     body=json.dumps(FIXTURES[path]))
                if path == "/api/jobs":
                    st = "new"
                    if "status=" in u:
                        st = u.split("status=")[1].split("&")[0]
                    return r.fulfill(status=200, content_type="application/json",
                                     body=json.dumps(JOBS.get(st, [])))
                return r.fulfill(status=200, content_type="application/json", body="{}")

            page.route("**/api/**", route)
            page.goto(base + "/app/index.html", wait_until="networkidle")
            page.wait_for_timeout(600)

            body = (page.inner_text("body") or "").strip()
            if len(body) < 40:
                problems.append("%s: the page rendered almost nothing (%d chars)" % (name, len(body)))
            if page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2"):
                problems.append("%s (%dpx): the page scrolls sideways" % (name, w))
            for e in errors:
                problems.append("%s: console error: %s" % (name, e[:160]))

            f = out / ("app-%s-%d.png" % (name, w))
            page.screenshot(path=str(f), full_page=True)
            shots.append(f)
            ctx.close()
        browser.close()
    httpd.shutdown()

    print("wrote %d screenshot(s) to %s" % (len(shots), out))
    for f in shots:
        print("   ", f.name, "%.0f KB" % (f.stat().st_size / 1024))
    if problems:
        print("\n%d problem(s):" % len(problems))
        for p in problems:
            print("  -", p)
        return 1
    print("\nNo sideways scroll, no console errors, content rendered at all three widths.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
