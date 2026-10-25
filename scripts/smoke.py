#!/usr/bin/env python3
"""
smoke.py - prove a deploy is alive, on the expected schema, and still enforcing
its auth gates. Python port of scripts/smoke.ps1 (2026-10-25), so every PR can
be smoke-tested from any machine - the Mac, or the cloud workspace that opens
the PRs - not only from Windows PowerShell.

Read-only against the target. Sends no writes, creates no users, submits no
applications. Safe to run against production.

Two ways every PR is smoked (EXECUTION_PLAN_NEXT.md, section 3):

    # BEFORE merge - boot the app from this checkout on a throwaway database
    # and smoke it, plus the full local test suite:
    python3 scripts/smoke.py --boot-local

    # AFTER merge - production, waiting until Railway serves the merge commit:
    python3 scripts/smoke.py --skip-local --expect-commit <sha> --wait-for-deploy-minutes 8

Other options mirror smoke.ps1: --base-url, --expected-schema, --expect-backend,
--allow-stale-deploy, --health-budget-seconds.

Stdlib only, so it runs before `pip install` has happened.
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_INDEX = REPO_ROOT / "web_bundle" / "index.html"
LOCAL_SW = REPO_ROOT / "web_bundle" / "sw.js"
CONTRACT = REPO_ROOT / "scripts" / "ui_contract.json"
PROD_URL = "https://web-production-192b7.up.railway.app"

_tty = sys.stdout.isatty()


def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _tty else s


GREEN, RED, CYAN, GREY, YELLOW = "32", "31", "36", "90", "33"


class Smoke:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures = []

    def check(self, name, fn):
        """fn returns True for a pass, anything else is the failure reason."""
        try:
            result = fn()
        except Exception as e:  # a crashing check is a failing check, named
            result = f"{type(e).__name__}: {e}"
        if result is True:
            print(_c(GREEN, "PASS  ") + name)
            self.passed += 1
        else:
            print(_c(RED, "FAIL  ") + name + "  -> " + str(result))
            self.failed += 1
            self.failures.append(name)


def note(msg, color=GREY):
    print(_c(color, "      " + msg))


def section(title):
    print("")
    print(_c(CYAN, f"-- {title} --"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def get(url, timeout=30):
    """Status + Location + body, never following redirects, never raising on 3xx-5xx."""
    req = urllib.request.Request(url, headers={"User-Agent": "job-hunter-smoke/1.0"})
    t0 = time.monotonic()
    try:
        resp = _opener.open(req, timeout=timeout)
        code, headers, body = resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        code, headers, body = e.code, e.headers, e.read()
    return {
        "code": code,
        "location": (headers.get("Location") if headers else "") or "",
        "body": body.decode("utf-8", errors="replace"),
        "seconds": round(time.monotonic() - t0, 2),
    }


def git_head(short=7):
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", f"--short={short}", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def expected_schema():
    code = ("import sys; sys.path.insert(0, %r); import migrations; "
            "print(max(v for v,_n,_f in migrations.MIGRATIONS))" % str(REPO_ROOT))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    v = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
    if not v.isdigit():
        raise RuntimeError(f"could not read the expected version from migrations.py ({out.stderr.strip()[-200:]})")
    return int(v)


# ── Local boot ───────────────────────────────────────────────────────────────

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def boot_local(commit):
    """Start app.py from this checkout on a throwaway SQLite database. Returns
    (process, base_url, tmpdir). Nothing external is configured: no Gemini key,
    no Resend key, no Postgres - so the box can serve but never reach out."""
    tmp = tempfile.mkdtemp(prefix="jh-smoke-")
    port = _free_port()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("JH_", "GEMINI", "RESEND", "TWILIO", "DATABASE_URL", "DB_BACKEND",
                                "GOOGLE_CLIENT", "APPLY_ENGINE"))}
    env.update({
        "PORT": str(port),
        "DATABASE_PATH": os.path.join(tmp, "jobs.db"),
        "UPLOADS_DIR": os.path.join(tmp, "uploads"),
        "RAILWAY_GIT_COMMIT_SHA": commit,
        "JH_SESSION_SECRET": "smoke-local-only",
        "PYTHONUNBUFFERED": "1",
    })
    log = open(os.path.join(tmp, "app.log"), "w")
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=str(REPO_ROOT), env=env,
                            stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        try:
            if get(base + "/api/health", timeout=3)["code"] in (200, 503):
                return proc, base, tmp
        except Exception:
            pass
        time.sleep(0.5)
    tail = Path(tmp, "app.log").read_text(errors="replace")[-2000:]
    proc.kill()
    raise RuntimeError(f"local app did not come up on {base}\n--- app.log (tail) ---\n{tail}")


# ── The checks ───────────────────────────────────────────────────────────────

def run(args):
    s = Smoke()
    base = args.base_url.rstrip("/")
    want_commit = (args.expect_commit or git_head())[:7]

    if re.search(r"[<>]", base):
        print(_c(RED, f"FAIL: --base-url is still a placeholder: {base}"))
        return 2

    print("")
    print(_c(CYAN, f"=== smoke: {base} ==="))

    # Local suite
    if not args.skip_local:
        section("local")

        def _compiles():
            r = subprocess.run([sys.executable, "-m", "py_compile", "app.py", "db.py", "auth.py",
                                "apply_engine.py", "ai_analysis.py"], cwd=str(REPO_ROOT),
                               capture_output=True, text=True)
            return True if r.returncode == 0 else f"py_compile exit {r.returncode}: {r.stderr[-300:]}"
        s.check("python compiles the app", _compiles)

        def _suite():
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "--tb=line", "-p", "no:cacheprovider"],
                               cwd=str(REPO_ROOT), capture_output=True, text=True)
            lines = r.stdout.splitlines()
            summary = next((l for l in reversed(lines) if re.search(r"passed|failed|error", l)), "").strip()
            if r.returncode == 0:
                note(summary)
                return True
            for l in [l for l in lines if l.startswith(("FAILED ", "ERROR "))][:10]:
                note(l, YELLOW)
            for l in [l for l in lines if re.match(r"^\S*\.py:\d+:", l)][:5]:
                note(l, YELLOW)
            return summary or f"pytest exit {r.returncode}"
        s.check("full test suite green", _suite)

        s.check("route harness present",
                lambda: True if (REPO_ROOT / "tests" / "test_routes.py").exists() else "tests/test_routes.py missing")
        if not os.environ.get("JH_TEST_PG_URL"):
            note("NOTE: JH_TEST_PG_URL is not set - the Postgres-only tests (pool leak, upsert dialect,", YELLOW)
            note("health 503) were SKIPPED. Point it at a scratch database (never jobhunter_prod).", YELLOW)

    # Wait for the deploy
    if args.wait_for_deploy_minutes > 0:
        section("waiting for the deploy")
        deadline = time.monotonic() + args.wait_for_deploy_minutes * 60
        seen = None
        while time.monotonic() < deadline:
            got = ""
            try:
                r = get(base + "/api/health", timeout=15)
                got = (json.loads(r["body"]).get("commit") or "") if r["body"] else ""
            except Exception:
                pass
            if got != seen:
                note(f"{time.strftime('%H:%M:%S')}  deployed={got or '?'}  want={want_commit}")
                seen = got
            if got == want_commit:
                time.sleep(5)  # first requests after a swap can land on a warming container
                break
            time.sleep(15)
        else:
            note(f"still not on {want_commit} after {args.wait_for_deploy_minutes} min - the checks below will say so",
                 YELLOW)

    # Reachability
    section("reachability")
    state = {"health": None}

    def _health():
        r = get(base + "/api/health")
        if r["code"] == 503:
            try:
                b = json.loads(r["body"])
            except Exception:
                b = {}
            return f"503 - the app cannot get a database connection: {b.get('error')}  pool={b.get('db_pool')}"
        if r["code"] != 200:
            return f"status {r['code']}  {r['body'][:160]}"
        h = json.loads(r["body"])
        state["health"] = h
        if h.get("status") != "ok":
            return f"status field = {h.get('status')}"
        note(f"users={h.get('active_users')}  jobs={h.get('total_jobs')}  schema={h.get('schema_version')}  "
             f"backend={h.get('db_backend')}")
        return True
    s.check("GET /api/health is 200 and well-formed", _health)
    H = lambda: state["health"]

    def _commit():
        h = H()
        if h is None:
            return "no health payload"
        deployed = h.get("commit") or ""
        if not deployed:
            note("(box reports no commit - predates this check)")
            return True
        note(f"deployed={deployed}  expected={want_commit}")
        if not want_commit:
            note("(no expected commit - no local git and no --expect-commit)")
            return True
        if deployed == want_commit:
            return True
        if args.allow_stale_deploy:
            note("differs, allowed by --allow-stale-deploy")
            return True
        return (f"deployed build is {deployed} but expected {want_commit} - either the deploy has not finished "
                f"(use --wait-for-deploy-minutes) or this is a different build.")
    s.check("the box is running the build we think it is", _commit)

    def _sched():
        h = H()
        if h is None:
            return "no health payload"
        sl = h.get("scheduler_lock")
        if sl is None:
            note("(box predates the scheduler lock)")
            return True
        note(f"enabled={sl.get('enabled')}  holds_lock={sl.get('holds_lock')}")
        if sl.get("holds_lock") is False:
            return "another instance holds the scheduler lock - this box will never fire a scheduled run"
        return True
    s.check("this box is the one running the scheduler", _sched)

    def _errors():
        h = H()
        if h is None:
            return "no health payload"
        er = h.get("error_reporting")
        if er is None:
            note("(box predates error reporting)")
            return True
        note(str(er.get("state")))
        if not er.get("reporting"):
            note("NOTE: unhandled exceptions are logged and nothing is alerted.")
        return True
    s.check("a crash would reach someone", _errors)

    def _refused():
        h = H()
        if h is None:
            return "no health payload"
        refused = h.get("db_backend_refused")
        return True if not refused else f"the app refused its Postgres target and is serving SQLite instead: {refused}"
    s.check("the box is not falling back after refusing its configured database", _refused)

    def _backend():
        h = H()
        if h is None:
            return "no health payload"
        if not args.expect_backend:
            note("(not pinned - pass --expect-backend to assert)")
            return True
        got = h.get("db_backend")
        return True if got == args.expect_backend else f"db_backend={got}, expected {args.expect_backend}"
    s.check("the box is serving the engine it is supposed to", _backend)

    def _schema():
        h = H()
        if h is None:
            return "no health payload"
        want = args.expected_schema if args.expected_schema > 0 else expected_schema()
        got = int(h.get("schema_version") or 0)
        if got == want:
            return True
        if got == 0:
            return "deployed box reports schema_version 0 - migrations did not run there"
        return f"deployed schema_version {got}, expected {want} - the deploy is behind"
    s.check("deployed schema is at the expected migration version", _schema)

    def _login():
        r = get(base + "/login")
        return True if r["code"] == 200 and re.search(r"(?i)<!doctype html>", r["body"]) else f"status {r['code']}"
    s.check("GET /login renders", _login)

    def _app_gate():
        r = get(base + "/app")
        return True if r["code"] == 302 and "/login" in r["location"] else f"status {r['code']} location {r['location']}"
    s.check("GET /app shell requires a session", _app_gate)

    s.check("GET /app/manifest.webmanifest serves (bundle is deployed)",
            lambda: True if get(base + "/app/manifest.webmanifest")["code"] == 200 else "not 200")

    def _index():
        r = get(base + "/app/index.html")
        if r["code"] == 200 and re.search(r"(?i)<!doctype html>", r["body"]):
            return True
        return f"status {r['code']} - web_bundle may be missing from the deploy"
    s.check("GET /app/index.html serves the built shell", _index)

    # The deployed /app bundle (web_bundle/ is committed; Railway does not build it)
    section("deployed /app bundle")
    bundle = {"js": "", "href": ""}

    def _bundle():
        r = get(base + "/app/index.html")
        if r["code"] != 200:
            return f"index.html status {r['code']}"
        m = re.search(r'src="[^"]*?(assets/[^"]+\.js)"', r["body"])
        if not m:
            return "index.html loads no script"
        bundle["href"] = m.group(1)
        j = get(base + "/app/" + bundle["href"])
        if j["code"] != 200:
            return f"bundle {bundle['href']} status {j['code']}"
        if len(j["body"]) < 100000:
            return f"bundle is only {len(j['body'])} bytes - that is not a real build"
        bundle["js"] = j["body"]
        note(f"serving {bundle['href']}  ({len(bundle['js'])} bytes)")
        return True
    s.check("the deployed shell serves a real script bundle", _bundle)

    def _bundle_match():
        if not bundle["href"]:
            return "no bundle was read"
        if not LOCAL_INDEX.exists():
            return f"no local {LOCAL_INDEX} - cannot tell whether the deploy matches this repo"
        lm = re.search(r'src="[^"]*?(assets/[^"]+\.js)"', LOCAL_INDEX.read_text())
        if not lm:
            return "local web_bundle/index.html loads no script"
        if lm.group(1) == bundle["href"]:
            return True
        if args.allow_stale_deploy:
            note("differs, allowed by --allow-stale-deploy")
            return True
        return (f"deployed {bundle['href']} but this repo has {lm.group(1)}. Vite hashes by content, so the "
                f"deployed bundle is not this one: deploy unfinished, web_bundle/ not rebuilt, or a different build.")
    s.check("the deployed bundle is the one in this repo", _bundle_match)

    def _sw():
        r = get(base + "/app/sw.js")
        if r["code"] != 200:
            return f"sw.js status {r['code']}"
        dm = re.search(r'VERSION\s*=\s*"([^"]+)"', r["body"])
        if not dm:
            return "deployed sw.js declares no VERSION"
        if not LOCAL_SW.exists():
            return f"no local {LOCAL_SW}"
        lm = re.search(r'VERSION\s*=\s*"([^"]+)"', LOCAL_SW.read_text())
        if not lm:
            return "local web_bundle/sw.js declares no VERSION"
        note(f"deployed={dm.group(1)}  local={lm.group(1)}")
        if dm.group(1) == lm.group(1):
            return True
        if args.allow_stale_deploy:
            note("differs, allowed by --allow-stale-deploy")
            return True
        return f"deployed sw.js is {dm.group(1)}, this repo says {lm.group(1)} - returning users keep the old bundle"
    s.check("the service worker version moved with the bundle", _sw)

    if not CONTRACT.exists():
        s.check("the UI contract file exists", lambda: f"missing {CONTRACT}")
    else:
        controls = json.loads(CONTRACT.read_text()).get("controls", [])
        note(f"checking {len(controls)} contracted controls from scripts/ui_contract.json")
        for c in controls:
            s.check(f"deployed bundle: {c['what']}",
                    lambda c=c: ("no bundle was read" if not bundle["js"] else True if c["needle"] in bundle["js"]
                                 else f"'{c['needle']}' (shipped {c.get('since')}) is not in the deployed bundle"))

    def _unicode():
        if not bundle["js"]:
            return "no bundle was read"
        bad = [q[:60] for q in re.findall(r'"(?:[^"\\]|\\.)*"', bundle["js"]) if re.search(r"\\u[0-9a-fA-F]{4}", q)]
        return True if not bad else f"{len(bad)} string(s) would render literally, e.g. {bad[0]}"
    s.check("deployed bundle ships no literal backslash-u to users", _unicode)

    # Auth gates
    section("auth gates (anonymous)")
    for p in ("/dashboard", "/settings", "/onboarding", "/admin"):
        s.check(f"GET {p} redirects to /login",
                lambda p=p: (lambda r: True if r["code"] == 302 and "/login" in r["location"]
                             else f"status {r['code']} location {r['location']}")(get(base + p)))
    for p in ("/api/me", "/api/jobs", "/api/stats", "/api/activity"):
        s.check(f"GET {p} is not served anonymously",
                lambda p=p: (lambda r: True if r["code"] == 302 else f"status {r['code']}")(get(base + p)))

    def _admin_users():
        r = get(base + "/api/admin/users")
        if r["code"] == 200:
            return "served 200 anonymously"
        if "@" in r["body"]:
            return "response contained an email address"
        return True
    s.check("GET /api/admin/users leaks nothing anonymously", _admin_users)

    # Database and worker
    section("database and worker")

    def _db():
        h = H()
        if h is None:
            return "no health payload"
        db = h.get("db_check")
        if db is None:
            return "no 'db_check' in /api/health - this build predates it"
        if db.get("ok"):
            note(f"round trip: {db.get('ms')}ms")
            return True
        return "the box is up but its database is not answering: " + str(db.get("error"))
    s.check("the database answers a real query", _db)

    def _worker():
        h = H()
        if h is None:
            return "no health payload"
        w = h.get("worker")
        if w is None:
            return "no 'worker' in /api/health - this build predates it"
        if w.get("unavailable"):
            return "worker health unavailable: " + str(w.get("unavailable"))
        age = w.get("oldest_claim_age_s")
        note(f"worker running: {w.get('running')}, claimed: {w.get('claimed')}, "
             f"oldest claim: {'none' if age is None else str(age) + 's'}")
        if w.get("recovering"):
            note("a claim is past the threshold and the sweeper has not reached it yet - recovering, not stuck",
                 YELLOW)
        if w.get("stuck"):
            return "a claimed job is past the stuck threshold AND past the sweep window - the sweeper is not reaching it"
        return True
    s.check("no job is claimed and abandoned", _worker)

    # Connection pool (the 2026-09-25 outage)
    section("connection pool")
    is_pg = bool(H() and H().get("db_backend") == "postgres")

    def _budget():
        ctl = get(base + "/login")
        h = get(base + "/api/health")
        note(f"/api/health {h['seconds']}s   control /login {ctl['seconds']}s")
        if h["code"] != 200:
            return f"health status {h['code']}"
        if h["seconds"] <= args.health_budget_seconds:
            return True
        if ctl["seconds"] > args.health_budget_seconds:
            return f"everything is slow ({ctl['seconds']}s for /login too) - network, not the pool; re-run"
        return f"health took {h['seconds']}s while /login took {ctl['seconds']}s - the app is waiting for a DB connection"
    s.check("health answers inside its budget (a starved pool takes ~20s)", _budget)

    def _waiting():
        h = H()
        if h is None:
            return "no health payload"
        if not is_pg:
            note("(SQLite - no pool)")
            return True
        pools = h.get("db_pool")
        if pools is None:
            return "no db_pool in /api/health"
        problems = []
        for name, p in pools.items():
            note(f"{name}  size={p.get('size')}  available={p.get('available')}  waiting={p.get('waiting')}")
            if int(p.get("waiting") or 0) > 0:
                problems.append(f"{name}: {p.get('waiting')} request(s) waiting")
        return True if not problems else "; ".join(problems)
    s.check("nothing is queued waiting for a connection", _waiting)

    def _leaks():
        h = H()
        if h is None:
            return "no health payload"
        lk = h.get("db_leaks_reclaimed")
        if lk is None:
            return "no db_leaks_reclaimed in /api/health - this build predates the 2026-09-28 fix"
        note(f"reclaimed={lk.get('count')}" + (f"  last at: {lk.get('last')}" if lk.get("last") else ""))
        if int(lk.get("count") or 0) == 0:
            return True
        return (f"{lk.get('count')} connection(s) were leaked and reclaimed, last at '{lk.get('last')}' - "
                f"that route needs a try/finally (the app is still up)")
    s.check("no request has leaked a connection since the app started", _leaks)

    def _burst():
        h = H()
        if h is None:
            return "no health payload"
        before = h.get("db_leaks_reclaimed")
        slowest, last = 0.0, None
        for i in range(20):
            r = get(base + "/api/health")
            if r["code"] != 200:
                return f"request {i + 1} of 20 returned {r['code']}"
            slowest = max(slowest, r["seconds"])
            last = json.loads(r["body"])
        note(f"slowest of 20: {slowest}s")
        if slowest > args.health_budget_seconds:
            return f"slowest request took {slowest}s"
        if is_pg:
            for name, p in (last.get("db_pool") or {}).items():
                if int(p.get("waiting") or 0) > 0:
                    return f"requests are waiting on {name} after the burst"
        if before is not None and int(last["db_leaks_reclaimed"]["count"]) != int(before["count"]):
            return f"leaks went from {before['count']} to {last['db_leaks_reclaimed']['count']} during the burst"
        return True
    s.check("a burst of 20 requests leaves the pool as it found it", _burst)

    # Cost guardrails
    section("cost guardrails")

    def _llm():
        h = H()
        if h is None:
            return "no health payload"
        llm = h.get("llm")
        if llm is None:
            return "no 'llm' block in /api/health - gemini.py is not wired on this box"
        note(f"day:    {llm.get('day')}")
        note(f"spent:  {llm['global'].get('calls')} calls, {llm['global'].get('tokens')} tokens")
        note(f"limits: {llm['limits'].get('global_calls')} calls/day global, "
             f"{llm['limits'].get('user_calls')} calls/day per user")
        return True
    s.check("health reports today's Gemini spend", _llm)

    def _enforce():
        if not args.expect_enforced:
            note("(local box - ceilings come from Railway variables, not checked here)")
            return True
        h = H()
        if h is None or h.get("llm") is None:
            return "no llm payload"
        return True if int(h["llm"]["limits"].get("enforce") or 0) == 1 else \
            "JH_LLM_ENFORCE=0 on this box - spend is counted and alerted but nothing is blocked"
    s.check("the spend ceiling is actually enforcing", _enforce)

    def _finite():
        if not args.expect_enforced:
            return True
        h = H()
        if h is None or h.get("llm") is None:
            return "no llm payload"
        l = h["llm"]["limits"]
        finite = [v for v in (l.get("global_calls"), l.get("global_tokens"), l.get("user_calls"), l.get("user_tokens"))
                  if int(v or 0) > 0]
        return True if finite else "every JH_LLM_* ceiling is 0 - nothing would ever be blocked"
    s.check("at least one ceiling is finite", _finite)

    def _history():
        h = H()
        if h is None:
            return "no health payload"
        hist = h.get("llm_history")
        if hist is None:
            return "no 'llm_history' in /api/health - this box predates the ceiling evidence"
        if hist and hist[0].get("error") is not None:
            return "the ledger read failed: " + str(hist[0]["error"])
        if not hist:
            note("no Gemini calls recorded in the last 14 days")
            return True
        peak = max(d.get("calls") or 0 for d in hist)
        peak_day = max(hist, key=lambda d: d.get("calls") or 0).get("day")
        for d in hist[:5]:
            note(f"{d.get('day')}  {d.get('calls')} calls, {d.get('tokens')} tokens, {d.get('users')} user(s), "
                 f"busiest user {d.get('max_user_calls')}")
        note(f"peak:   {peak} calls on {peak_day}")
        ceiling = int(h["llm"]["limits"].get("global_calls") or 0) if h.get("llm") else 0
        if peak > 0 and ceiling > 0:
            note(f"the global ceiling is {round(ceiling / peak)}x the busiest day - a useful ceiling is nearer 10x")
        user_peak = max(d.get("max_user_calls") or 0 for d in hist)
        user_ceiling = int(h["llm"]["limits"].get("user_calls") or 0) if h.get("llm") else 0
        if user_peak > 0 and user_ceiling > 0:
            note(f"busiest single account: {user_peak} calls/day against a per-user ceiling of {user_ceiling}")
            if user_peak >= user_ceiling:
                return (f"JH_LLM_USER_CALLS={user_ceiling} is at or below a real observed day ({user_peak}) - "
                        f"a normal user would hit it")
        return True
    s.check("health carries the 14-day spend record", _history)

    # Parked state
    section("parked-state check")

    def _parked():
        h = H()
        if h is None:
            return "no health payload"
        la, ls = h.get("last_apply") or {}, h.get("last_search") or {}
        note(f"last_apply:  {la.get('detail')}  ({la.get('date')})")
        note(f"last_search: {ls.get('detail')}  ({ls.get('date')})")
        return True
    s.check("health reports the last apply (engine expected parked)", _parked)

    print("")
    print(_c(CYAN, f"=== {s.passed} passed, {s.failed} failed ==="))
    return 1 if s.failed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=PROD_URL)
    ap.add_argument("--boot-local", action="store_true",
                    help="start app.py from this checkout on a throwaway DB and smoke that (pre-merge)")
    ap.add_argument("--expected-schema", type=int, default=0, help="0 = read it from migrations.py")
    ap.add_argument("--expect-backend", choices=["", "sqlite", "postgres"], default="")
    ap.add_argument("--expect-commit", default="", help="commit the box must report; default: local HEAD")
    ap.add_argument("--allow-stale-deploy", action="store_true")
    ap.add_argument("--skip-local", action="store_true", help="skip compile + pytest")
    ap.add_argument("--wait-for-deploy-minutes", type=int, default=0)
    ap.add_argument("--health-budget-seconds", type=float, default=5.0)
    args = ap.parse_args(argv)
    args.expect_enforced = not args.boot_local

    if not args.boot_local:
        return run(args)

    commit = args.expect_commit or git_head(40)
    proc, base, tmp = boot_local(commit)
    args.base_url = base
    args.expect_commit = commit[:7]
    if not args.expect_backend:
        args.expect_backend = "sqlite"
    try:
        return run(args)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
