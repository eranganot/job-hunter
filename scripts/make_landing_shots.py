"""
make_landing_shots.py - takes the landing page's product pictures from the real
/app, run locally against a demo account. Every company, person and number in
them is invented here; no real user's data is involved.

    pip install playwright pillow && playwright install chromium
    python3 scripts/make_landing_shots.py            # writes assets/landing/*.webp

What it does: boots this checkout on a throwaway SQLite database (as
scripts/smoke.py --boot-local does), registers "Maya Levi", fills her account
with invented jobs, a cover letter and a tailored CV, opens /app in headless
Chromium and saves:

    job-card.webp     the swipe card at desktop width (two columns)
    job-details.webp  a role's details: the finished cover letter and the
                      tailored CV with what the job asks for that the CV lacks
    analytics.webp    the statistics tiles (queue, applied, deferred, passed)

The approval-rate tile is not in the picture (panel review, 2026-11-13): a
rate of passes sits badly under "only the ones that fit". The picture is a
crop of the real screen, nothing on it is hidden or edited.

Build-time only: the app never imports Playwright or Pillow for this.
Set CHROMIUM=/path/to/chrome when Playwright's own browser is not installed.
"""
import datetime
import http.client
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "landing")

COVER_LETTER = """Dear Northwind Labs team,

Your Growth team is looking for someone to own activation and early retention, and that has been my job for the last three years. At Brightside I led onboarding for our consumer app: we rebuilt the first-week experience over four experiments and lifted week-one retention from 31% to 38%.

What draws me to this role is the mix you describe: a product manager who sets the roadmap for the growth squad and still runs the experiments personally. I have managed two product managers while keeping one funnel of my own, and I would like to keep working that way.

I would be glad to walk you through the onboarding work and what I would try first at Northwind Labs.

Best regards,
Maya Levi"""

TAILORED = {
    "cv": {
        "headline": "Senior Product Manager, Growth",
        "summary": "Product manager with six years in B2C products, the last three leading onboarding and activation. "
                   "Runs experiments end to end with design, data and engineering.",
        "skills": ["Onboarding and activation", "A/B testing", "Funnel analytics", "SQL", "Roadmapping", "Team leadership"],
        "experience": [
            {"title": "Group Product Manager", "company": "Brightside", "dates": "2023 - Present",
             "bullets": ["Own onboarding and first-week retention for a consumer app with 400K monthly users",
                         "Lifted week-one retention from 31% to 38% across four onboarding experiments",
                         "Manage two product managers; set the quarterly roadmap with design, data and engineering"]},
            {"title": "Product Manager", "company": "Kite Mobile", "dates": "2020 - 2023",
             "bullets": ["Ran the signup funnel: 22 experiments in two years, signup completion up 17%",
                         "Built the weekly funnel dashboard the team still uses"]},
        ],
        "education": ["B.Sc. Industrial Engineering and Management, 2019"],
        "other": [],
        "changes": ["Moved onboarding and retention results to the top of each role",
                    "Reworded the summary around growth and experiments, which this role asks for",
                    "Left out the 2019 internship to keep it to one page"],
    },
    "match": [
        {"need": "5+ years in B2C product management", "evidence": "Six years across Brightside and Kite Mobile"},
        {"need": "Shipping experiments at scale", "evidence": "26 onboarding and signup experiments, with results"},
        {"need": "Managing product managers", "evidence": "Manages two product managers at Brightside"},
        {"need": "Experience with paid acquisition", "evidence": ""},
    ],
    "removed": [],
    "flags": [],
}

DESCRIPTION = ("We are looking for a senior product manager to own activation and early retention for our consumer app. "
               "You will own the onboarding funnel and its metrics, run experiments with design, data and engineering, "
               "and set the roadmap for the growth squad. We are looking for 5+ years in B2C product management and a "
               "track record of shipping experiments at scale.")

# title, company, status, applied_via
JOBS = [("Senior Product Manager, Growth", "Northwind Labs", "approved", None)]
JOBS += [("Senior Product Manager, Growth", "Northwind Labs", "new", None)]      # the same role, still to swipe
JOBS += [(t, c, "approved", None) for t, c in (
    ("Product Manager, Onboarding", "Orbitly"), ("Senior Product Manager", "Lumen Health"),
    ("Product Lead, Activation", "Kesef Labs"), ("Group Product Manager", "Tavor Systems"),
    ("Senior PM, Consumer", "Alma Mobile"))]
JOBS += [("Product Manager %d" % i, "Applied Co %d" % i, "applied", "engine" if i % 3 else "manual") for i in range(1, 10)]
JOBS += [("Product Manager %d" % i, "Later Co %d" % i, "deferred", None) for i in range(1, 3)]
JOBS += [("Product Manager %d" % i, "Passed Co %d" % i, "rejected", None) for i in range(1, 15)]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def boot(tmp):
    port = _free_port()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("JH_", "GEMINI", "RESEND", "TWILIO", "DATABASE_URL", "DB_BACKEND",
                                "GOOGLE_CLIENT", "APPLY_ENGINE", "META_"))}
    env.update({"PORT": str(port), "DATABASE_PATH": os.path.join(tmp, "jobs.db"),
                "UPLOADS_DIR": os.path.join(tmp, "uploads"), "JH_SESSION_SECRET": "shots-local-only",
                "PYTHONUNBUFFERED": "1"})
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=ROOT, env=env,
                            stdout=open(os.path.join(tmp, "app.log"), "w"), stderr=subprocess.STDOUT)
    for _ in range(90):
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            c.request("GET", "/api/health")
            if c.getresponse().status in (200, 503):
                return proc, port
        except OSError:
            time.sleep(1)
    proc.kill()
    raise SystemExit("the app did not start; see %s" % os.path.join(tmp, "app.log"))


def register(port):
    body = urllib.parse.urlencode({"name": "Maya Levi", "email": "maya@example.test",
                                   "password": "correct-horse-1", "password2": "correct-horse-1"})
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.request("POST", "/register", body, {"Content-Type": "application/x-www-form-urlencoded",
                                          "Origin": "http://127.0.0.1:%d" % port})
    r = c.getresponse()
    r.read()
    cookie = (r.getheader("Set-Cookie") or "").split(";")[0]
    if r.status != 302 or "=" not in cookie:
        raise SystemExit("register failed: %s" % r.status)
    return cookie.split("=", 1)


def seed(db_path):
    conn = sqlite3.connect(db_path)
    uid = conn.execute("SELECT id FROM users WHERE email='maya@example.test'").fetchone()[0]
    conn.execute("UPDATE users SET email_verified_at=datetime('now') WHERE id=?", (uid,))
    conn.execute("UPDATE user_profiles SET onboarding_complete=1, onboarding_dismissed=1, cv_analyzed=1, "
                 "cv_filename='Maya Levi CV.pdf', job_titles=?, locations=?, keywords=? WHERE user_id=?",
                 (json.dumps(["Senior Product Manager", "Group Product Manager"]), json.dumps(["Tel Aviv"]),
                  json.dumps(["growth", "onboarding"]), uid))
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    first = None
    for i, (title, company, status, via) in enumerate(JOBS):
        found = (now - datetime.timedelta(days=1 + i % 9)).strftime("%Y-%m-%d %H:%M:%S")
        main = i == 0
        rank = i
        if status == "new":
            found, rank = now.strftime("%Y-%m-%d %H:%M:%S"), 0
        cur = conn.execute(
            "INSERT INTO jobs (user_id, title, company, location, url, description, full_description, why_relevant, "
            "source, found_date, status, applied_date, applied_via, rejected_by, url_verified, match_score, "
            "candidate_score, cover_letter, status_check, status_checked_date, released_on) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, title, company, "Tel Aviv (Hybrid)",
             "https://boards.greenhouse.io/example/jobs/%d" % (4000 + i), DESCRIPTION, DESCRIPTION,
             "Six years in B2C product match the 5+ they ask for. You led onboarding experiments end to end, which is "
             "the core of this role, and you have managed product managers before.",
             "greenhouse", found, status, found if status == "applied" else None, via,
             "user" if status == "rejected" else None, 1, 92 - rank % 20, 92 - rank % 15,
             COVER_LETTER if main else None, "open" if main else None, found if main else None, found[:10]))
        first = first or cur.lastrowid
    conn.execute("INSERT INTO tailored_cvs (user_id, job_id, content, created_at, edited, use_for_apply) "
                 "VALUES (?,?,?,?,0,1)", (uid, first, json.dumps(TAILORED), now.strftime("%Y-%m-%d %H:%M:%S")))
    # one cover letter and one tailored CV were made this month, so "9 of 10 left" is what the screen says
    for meter in ("cover_letter", "tailored_cv"):
        conn.execute("INSERT INTO usage_counters (user_id, meter, period, uses, updated_date) VALUES (?,?,?,1,?)",
                     (uid, meter, now.strftime("%Y-%m"), now.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    return first


def save_webp(png_bytes, name, w=1400):
    from PIL import Image
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    im = im.resize((w, round(im.size[1] * w / im.size[0])), Image.LANCZOS)
    path = os.path.join(OUT, name)
    im.save(path, "WEBP", quality=82, method=6)
    print("wrote %s %dx%d (%d bytes)" % (path, im.size[0], im.size[1], os.path.getsize(path)))


def main():
    from playwright.sync_api import sync_playwright
    tmp = tempfile.mkdtemp(prefix="jh-shots-")
    proc, port = boot(tmp)
    try:
        name, value = register(port)
        seed(os.path.join(tmp, "jobs.db"))
        base = "http://127.0.0.1:%d" % port
        with sync_playwright() as p:
            kw = {"executable_path": os.environ["CHROMIUM"]} if os.environ.get("CHROMIUM") else {}
            b = p.chromium.launch(**kw)
            # the swipe card as a desktop shows it (job-card.webp, the landing's main picture);
            # first, while the job is still waiting to be reviewed
            ctx = b.new_context(viewport={"width": 1100, "height": 1100}, device_scale_factor=2,
                                timezone_id="Asia/Jerusalem", locale="en-GB")
            ctx.add_cookies([{"name": name, "value": value, "url": base}])
            take_card(ctx.new_page(), base)
            ctx.close()
            for wide in (False, True):
                ctx = b.new_context(viewport={"width": 780 if wide else 390, "height": 1000},
                                    device_scale_factor=2 if wide else 3,
                                    timezone_id="Asia/Jerusalem", locale="en-GB")
                ctx.add_cookies([{"name": name, "value": value, "url": base}])
                pg = ctx.new_page()
                if wide:
                    take(pg, base)
                else:
                    take_phone(pg, base, os.path.join(tmp, "jobs.db"))
                ctx.close()
            b.close()
    finally:
        proc.terminate()


PHONE_W = 780       # the phone pictures are saved 780px wide: 2x the widest phone column


def _span(first, last, pad=10):
    """A clip from the top of one box to the bottom of another."""
    return {"x": first["x"] - pad, "y": first["y"] - pad, "width": first["width"] + 2 * pad,
            "height": last["y"] + last["height"] - first["y"] + 2 * pad}


def take_card(pg, base):
    """job-card.webp: the whole swipe card at desktop width (two columns), buttons included."""
    pg.goto(base + "/app")
    pg.wait_for_selector("text=Why Noa picked this")
    pg.wait_for_timeout(800)
    card = pg.locator("text=Northwind Labs").first.locator("xpath=ancestor::div[contains(@class,'rounded')][last()]")
    save_webp(card.screenshot(), "job-card.webp")


def take_phone(pg, base, db_path):
    """The same three pictures as a phone shows them (390px wide), for visitors on phones."""
    # 1. the swipe card: with a job still to review, /app opens on it
    pg.goto(base + "/app")
    pg.wait_for_selector("text=Why Noa picked this")
    pg.wait_for_timeout(500)
    card = pg.locator("text=Northwind Labs").first.locator("xpath=ancestor::div[contains(@class,'rounded')][last()]")
    top = pg.locator("text=Northwind Labs").first.locator("xpath=../..").bounding_box()
    why = pg.locator("text=Why Noa picked this").locator("xpath=../..").bounding_box()
    box = card.bounding_box()
    clip = {"x": box["x"], "y": top["y"] - 26, "width": box["width"], "height": why["y"] + why["height"] - top["y"] + 50}
    save_webp(pg.screenshot(clip=clip), "job-card-mobile.webp", PHONE_W)

    # 2. swipe it away (in the database), so the dashboard and its tiles show
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM jobs WHERE status='new'")
    conn.commit()
    conn.close()
    pg.goto(base + "/app")
    pg.wait_for_timeout(2500)
    if not pg.locator("text=6 in queue").count():        # the "all caught up" screen: go to the dashboard
        pg.locator("button:has(svg.lucide-layout-grid)").first.click()
    pg.wait_for_selector("text=6 in queue")
    a = pg.locator("text=Ready to apply").locator("xpath=..").bounding_box()
    z = pg.locator("text=You passed").locator("xpath=..").bounding_box()
    clip = {"x": a["x"] - 10, "y": a["y"] - 10, "width": z["x"] + z["width"] - a["x"] + 20,
            "height": z["y"] + z["height"] - a["y"] + 20}
    save_webp(pg.screenshot(clip=clip), "analytics-mobile.webp", PHONE_W)

    # 3. the role's details: the cover letter down to the end of "How your CV meets this job"
    pg.set_viewport_size({"width": 390, "height": 4200})
    pg.click("text=Senior Product Manager, Growth")
    pg.wait_for_selector("text=How your CV meets this job")
    pg.wait_for_timeout(600)
    top = pg.locator("h4:has-text('Cover letter')").locator("xpath=..").bounding_box()
    end = pg.locator("text=How your CV meets this job").locator("xpath=..").bounding_box()
    save_webp(pg.screenshot(clip=_span(top, end)), "job-details-mobile.webp", PHONE_W)


def take(pg, base):
    pg.goto(base + "/app")
    pg.wait_for_selector("text=6 in queue")

    # the four statistics tiles: from the top of "Queue" to the bottom of "Passed"
    a = pg.locator("text=Ready to apply").locator("xpath=..").bounding_box()
    z = pg.locator("text=You passed").locator("xpath=..").bounding_box()
    pad = 14
    clip = {"x": a["x"] - pad, "y": a["y"] - pad, "width": z["x"] + z["width"] - a["x"] + 2 * pad,
            "height": a["height"] + 2 * pad}
    save_webp(pg.screenshot(clip=clip), "analytics.webp")

    # a role's details: the cover letter card down to "What changed for this job"
    pg.set_viewport_size({"width": 780, "height": 2600})          # tall, so the whole panel is laid out
    pg.click("text=Senior Product Manager, Growth")
    pg.wait_for_selector("text=How your CV meets this job")
    pg.wait_for_timeout(600)
    top = pg.locator("h4:has-text('Cover letter')").locator("xpath=..").bounding_box()
    end = pg.locator("text=What changed for this job").locator("xpath=..").bounding_box()
    clip = {"x": top["x"] - pad, "y": top["y"] - pad, "width": top["width"] + 2 * pad,
            "height": end["y"] + end["height"] - top["y"] + 2 * pad}
    save_webp(pg.screenshot(clip=clip), "job-details.webp")


if __name__ == "__main__":
    main()
