#!/usr/bin/env python3
"""Load test for STAGING (2026-11-24, launch item C12).

    python3 scripts/loadtest.py --base https://<staging host> --key <JH_LOADTEST_KEY> \
        --users 300 --concurrency 60 --minutes 10 [--visitors 40] [--search-every 0]

What it does, in this order:
  1. signs up --users accounts (loadtest-<run>-<n>@job-hunter.test - the test
     domain, so they never count as real users), using X-Loadtest-Key so the
     per-IP sign-up limit does not stop one machine;
  2. runs --concurrency signed-in "users" for --minutes, each looping like the
     app does: the /app shell, /api/me, /api/stats, the jobs list - plus, if
     --search-every N > 0, one "Find jobs" press every N loops (staging must run
     JH_FAKE_AI=1 so this costs nothing);
  3. runs --visitors anonymous landing-page visitors alongside;
  4. prints a table per endpoint (count, errors, p50/p95/p99 ms) and writes the
     same as JSON (--out). Exit code 1 if errors exceed --max-error-pct or the
     signed-in p95 exceeds --max-p95-ms.

It refuses production hosts. Production is tested read-only by smoke.py.
"""
import argparse
import json
import random
import statistics
import string
import sys
import threading
import time
from collections import defaultdict

import httpx

PROD_HOSTS = ("job-hunter.work", "web-production-192b7.up.railway.app")
TEST_DOMAIN = "job-hunter.test"
PASSWORD = "loadtest-Pw-2026!"

_lock = threading.Lock()
_stats = defaultdict(lambda: {"n": 0, "err": 0, "limited": 0, "ms": []})


def _rec(name, ms, status, exc=None):
    """A signed-in call that is redirected or refused counts as an error: a
    load test whose requests all bounce to /login must never read as a pass."""
    signed_in = " /api/" in name or name.startswith("GET /app")
    bad = (exc is not None or status == 0 or status >= 500
           or (status >= 400 and status != 429)
           or (signed_in and status in (301, 302, 303)))
    with _lock:
        s = _stats[name]
        s["n"] += 1
        s["ms"].append(ms)
        s.setdefault("codes", defaultdict(int))[status] += 1
        if status == 429:
            s["limited"] += 1
        elif bad:
            s["err"] += 1


def _call(c, name, method, url, **kw):
    t = time.perf_counter()
    try:
        r = c.request(method, url, **kw)
        _rec(name, (time.perf_counter() - t) * 1000, r.status_code)
        return r
    except httpx.HTTPError as e:
        _rec(name, (time.perf_counter() - t) * 1000, 0, e)
        return None


def _client(base, key=None):
    h = {"User-Agent": "job-hunter-loadtest/1"}
    if key:
        h["X-Loadtest-Key"] = key
    return httpx.Client(base_url=base, headers=h, timeout=30.0, follow_redirects=False)


def signup(base, key, email):
    c = _client(base, key)
    r = _call(c, "POST /register", "POST", "/register",
              data={"name": "Load Test", "email": email, "password": PASSWORD, "password2": PASSWORD})
    if r is None or r.status_code not in (302, 303) or not c.cookies.get("session"):
        r = _call(c, "POST /login", "POST", "/login", data={"email": email, "password": PASSWORD})
    tok = c.cookies.get("session")
    if not tok:
        c.close()
        return None
    # The app's cookie is Secure; re-set it plain so a local http run sends it
    # too (on https staging this changes nothing).
    c.cookies.clear()
    c.cookies.set("session", tok)
    return c


def user_loop(c, stop, search_every):
    i = 0
    while not stop.is_set():
        i += 1
        _call(c, "GET /app/", "GET", "/app/")
        _call(c, "GET /api/me", "GET", "/api/me")
        _call(c, "GET /api/stats", "GET", "/api/stats")
        _call(c, "GET /api/jobs", "GET", "/api/jobs", params={"status": "new"})
        if search_every and i % search_every == 0:
            _call(c, "POST /api/run-search", "POST", "/api/run-search", json={})
        time.sleep(random.uniform(1.0, 3.0))          # a person reading the screen


def visitor_loop(base, stop):
    c = _client(base)
    while not stop.is_set():
        _call(c, "GET / (landing)", "GET", "/")
        _call(c, "GET /privacy", "GET", "/privacy")
        time.sleep(random.uniform(2.0, 5.0))
    c.close()


def pct(v, p):
    if not v:
        return 0.0
    v = sorted(v)
    return v[min(len(v) - 1, int(round(p / 100.0 * (len(v) - 1))))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True)
    ap.add_argument("--key", required=True, help="the staging JH_LOADTEST_KEY")
    ap.add_argument("--users", type=int, default=300)
    ap.add_argument("--concurrency", type=int, default=60)
    ap.add_argument("--minutes", type=float, default=10)
    ap.add_argument("--visitors", type=int, default=20)
    ap.add_argument("--search-every", type=int, default=0)
    ap.add_argument("--signup-threads", type=int, default=10)
    ap.add_argument("--max-error-pct", type=float, default=1.0)
    ap.add_argument("--max-p95-ms", type=float, default=1500)
    ap.add_argument("--out", default="loadtest-report.json")
    a = ap.parse_args()

    base = a.base.rstrip("/")
    if any(h in base for h in PROD_HOSTS):
        sys.exit("Refusing to load-test production (%s). Use the staging address." % base)

    run = "".join(random.choice(string.ascii_lowercase) for _ in range(5))
    print("run %s: signing up %d users on %s" % (run, a.users, base))
    clients, lock = [], threading.Lock()

    def _signup(n):
        c = signup(base, a.key, "loadtest-%s-%d@%s" % (run, n, TEST_DOMAIN))
        if c:
            with lock:
                clients.append(c)

    t0 = time.time()
    queue = list(range(a.users))
    def _worker():
        while True:
            with lock:
                if not queue:
                    return
                n = queue.pop()
            _signup(n)
    ws = [threading.Thread(target=_worker) for _ in range(a.signup_threads)]
    [w.start() for w in ws]
    [w.join() for w in ws]
    print("  %d of %d signed in after %.0fs" % (len(clients), a.users, time.time() - t0))
    if not clients:
        sys.exit("No user could sign in - is JH_LOADTEST_KEY set on staging and the key right?")

    stop = threading.Event()
    active = clients[:a.concurrency]
    threads = [threading.Thread(target=user_loop, args=(c, stop, a.search_every), daemon=True) for c in active]
    threads += [threading.Thread(target=visitor_loop, args=(base, stop), daemon=True) for _ in range(a.visitors)]
    print("running %d signed-in users + %d visitors for %.1f min" % (len(active), a.visitors, a.minutes))
    t1 = time.time()
    [t.start() for t in threads]
    try:
        time.sleep(a.minutes * 60)
    except KeyboardInterrupt:
        print("stopping early")
    stop.set()
    [t.join(timeout=35) for t in threads]
    secs = time.time() - t1
    for c in clients:
        c.close()

    report, total, errs, signed_ms = {}, 0, 0, []
    print("\n%-24s %7s %6s %7s %7s %7s %7s  %s" % ("endpoint", "count", "errors", "429s", "p50", "p95", "p99", "codes"))
    for name in sorted(_stats):
        s = _stats[name]
        total += s["n"]
        errs += s["err"]
        if name.startswith("GET /api"):
            signed_ms += s["ms"]
        row = {"count": s["n"], "errors": s["err"], "rate_limited": s["limited"],
               "p50_ms": round(pct(s["ms"], 50)), "p95_ms": round(pct(s["ms"], 95)),
               "p99_ms": round(pct(s["ms"], 99)), "mean_ms": round(statistics.mean(s["ms"])) if s["ms"] else 0,
               "status_codes": {str(k): v for k, v in sorted(s.get("codes", {}).items())}}
        report[name] = row
        print("%-24s %7d %6d %7d %7d %7d %7d  %s" % (name, row["count"], row["errors"], row["rate_limited"],
                                                 row["p50_ms"], row["p95_ms"], row["p99_ms"], row["status_codes"]))
    err_pct = 100.0 * errs / total if total else 0.0
    p95 = pct(signed_ms, 95)
    summary = {"run": run, "base": base, "signed_up": len(clients), "concurrency": len(active),
               "visitors": a.visitors, "seconds": round(secs), "requests": total,
               "rps": round(total / secs, 1) if secs else 0, "error_pct": round(err_pct, 2),
               "api_p95_ms": round(p95)}
    print("\n" + json.dumps(summary))
    with open(a.out, "w") as f:
        json.dump({"summary": summary, "endpoints": report}, f, indent=2)
    ok = err_pct <= a.max_error_pct and p95 <= a.max_p95_ms
    print("RESULT: %s (errors %.2f%% <= %.1f%%, API p95 %d ms <= %d ms)"
          % ("PASS" if ok else "FAIL", err_pct, a.max_error_pct, p95, a.max_p95_ms))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
