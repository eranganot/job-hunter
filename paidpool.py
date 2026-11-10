"""
paidpool.py - the shared pool of jobs from paid sources (2026-11-09).

Why a pool and not per-user calls
---------------------------------
Paid job APIs bill per JOB RETURNED. The Israeli market is finite, and users
looking for similar roles would buy the same postings again and again. So each
paid source is asked ONCE a day for Israel's new postings (titles = the union
of what the users who can see paid sources are looking for), the results are
kept in `paid_pool_jobs`, and every user's search reads from that table at no
cost. Cost now grows with the market, not with the number of users.
Fantastic.jobs' own docs recommend exactly this pattern.

The free-tier guarantee (The owner: "make sure that you will not pass the free tier")
------------------------------------------------------------------------------
* Every unit is counted in `paid_source_spend` (Postgres in production), not in
  /tmp - a redeploy cannot reset it.
* Spend is RESERVED before the call (the whole request `limit`, atomically, and
  only if it fits under the monthly cap) and trimmed to what actually came back
  afterwards. A crash mid-call over-counts; it can never under-count.
* One attempt per call, never a retry: a retried paid request can be billed twice.
* Each source's monthly cap is at most its free allowance (HARD_LIMIT below),
  whatever an env variable says, unless JH_POOL_ALLOW_ABOVE_FREE=1.
* The month's remaining units are spread over the days left, so the pool
  cannot spend the month on day one.
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    _LOCAL = ZoneInfo("Asia/Jerusalem")
except ImportError:                    # pragma: no cover - tz database missing
    _LOCAL = timezone(timedelta(hours=2))

import log as _log
print = _log.make_print(__name__)  # noqa: A001 - see log.py


# ── the sources ────────────────────────────────────────────────────────────
# unit = what the provider bills (all four: one job / dataset row returned).
# hard_limit = the free allowance per billing cycle; the cap can never exceed it.
# default_cap leaves a margin under it. Override down with JH_POOL_CAP_<NAME>.
# cycle_day = the day of the month the provider's own billing cycle starts (UTC).
# All read off each provider's dashboard on 2026-11-09 (The owner signed in):
#   TheirStack  Free plan, 200 API credits, cycle Oct 28 - Nov 28, auto-recharge off
#   Hirebase    Free plan, cycle Nov 1 - Nov 30, no paid add-ons
#   Fantastic   14-day trial Nov 9 - Nov 23: 500 jobs + 100 requests, no overage, no card
#   Apify       Free plan, $5 usage, cycle Oct 29 - Nov 28 UTC, pauses at $5, no card
SOURCES = {
    "theirstack": {"label": "TheirStack", "keys": ("THEIRSTACK_API_KEY",),
                   "hard_limit": 200, "default_cap": 180, "per_run_max": 25, "cycle_day": 28},
    "hirebase":   {"label": "Hirebase", "keys": ("HIREBASE_API_KEY", "HIREBASE_KEY"),
                   "hard_limit": 500, "default_cap": 450, "per_run_max": 40, "cycle_day": 1},
    # 500 jobs per 2-week trial period; one request a day stays far inside the
    # 100-request allowance. A monthly cycle anchored on the 9th with cap 450 can
    # never put more than 450 into the 14-day trial window.
    "fantastic":  {"label": "Fantastic.jobs",
                   "keys": ("FANTASTIC_JOBS_API_KEY", "FANTASTIC_API_KEY", "FANTASTICJOBS_API_KEY"),
                   "hard_limit": 500, "default_cap": 450, "per_run_max": 30, "cycle_day": 9},
    # Apify free plan: $5 of usage per cycle. Ethosia ~ $1.12 per 1,000 rows, so
    # 1,000 rows ~ $1.12 + run costs - far inside $5.
    "apify":      {"label": "Apify (Ethosia)", "keys": ("APIFY_TOKEN",),
                   "hard_limit": 1500, "default_cap": 1000, "per_run_max": 60, "cycle_day": 29},
}

# what jobs.source says for a pool job (Apify's actor is Ethosia's board)
SOURCE_NAME = {"theirstack": "theirstack", "hirebase": "hirebase",
               "fantastic": "fantastic.jobs", "apify": "ethosia"}

def enabled_sources() -> list:
    """Sources the owner has switched on: JH_PAID_POOL_SOURCES, a comma list such as
    "hirebase,theirstack". Empty or unset = OFF: no paid source is called at all,
    whatever keys are set (2026-11-09: plan each source before it runs)."""
    raw = os.environ.get("JH_PAID_POOL_SOURCES", "")
    return [n for n in (x.strip().lower() for x in raw.split(",")) if n in SOURCES]


RUN_AFTER_HOUR = 6           # Israel time: the daily pull happens after 06:00
POOL_MAX_AGE_DAYS = 14       # a user's search reads pool jobs fetched this recently
MAX_TITLES = 12              # titles per provider request
HTTP_TIMEOUT = 40
APIFY_TIMEOUT = 280          # run-sync waits for the actor (Apify caps it at 300s)
ETHOSIA_ACTOR = "getascraper~ethosia-scraper"

_RUN_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _anchor(y, m, day):
    """`day` of month (y, m), clamped to the month's length (the 29th of February)."""
    nxt = (datetime(y, m, 28) + timedelta(days=4)).replace(day=1)
    return datetime(y, m, min(day, (nxt - timedelta(days=1)).day)).date()


def _cycle_bounds(name, now=None):
    """(start, next_start) dates (UTC) of the provider's current billing cycle."""
    day = int(SOURCES[name].get("cycle_day", 1))
    today = (now or _now()).astimezone(timezone.utc).date()
    start = _anchor(today.year, today.month, day)
    if today < start:
        py, pm = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        start = _anchor(py, pm, day)
    ny, nm = (start.year, start.month + 1) if start.month < 12 else (start.year + 1, 1)
    return start, _anchor(ny, nm, day)


def _cycle(name, now=None) -> str:
    """The ledger key for the provider's current billing cycle. A cycle that
    starts on the 1st keeps the old "YYYY-MM" key, so units counted before
    cycles existed (Hirebase's first run) still count."""
    start, _nxt = _cycle_bounds(name, now)
    if int(SOURCES[name].get("cycle_day", 1)) == 1:
        return start.strftime("%Y-%m")
    return start.isoformat()


def _day(now=None) -> str:
    return (now or _now()).astimezone(_LOCAL).strftime("%Y-%m-%d")


def _days_left_in_cycle(name, now=None) -> int:
    _start, nxt = _cycle_bounds(name, now)
    return max(1, (nxt - (now or _now()).astimezone(timezone.utc).date()).days)


def api_key(name: str) -> str:
    for k in SOURCES[name]["keys"]:
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return ""


def cap(name: str) -> int:
    """The monthly cap in billed units: the env override, never above the
    free allowance unless JH_POOL_ALLOW_ABOVE_FREE=1."""
    s = SOURCES[name]
    try:
        want = int(os.environ.get("JH_POOL_CAP_%s" % name.upper(), s["default_cap"]))
    except ValueError:
        want = s["default_cap"]
    if os.environ.get("JH_POOL_ALLOW_ABOVE_FREE") == "1":
        return max(0, want)
    return max(0, min(want, s["hard_limit"]))


# ── the spend ledger ───────────────────────────────────────────────────────

def spent(conn, name: str, now=None) -> int:
    r = conn.execute("SELECT units FROM paid_source_spend WHERE provider=? AND month=?",
                     (name, _cycle(name, now))).fetchone()
    return int(r[0]) if r else 0


def reserve(conn, name: str, units: int, now=None) -> bool:
    """Atomically add `units` to this billing cycle's spend if it stays within
    the cap. (The column is still called `month`; it holds the cycle key.)"""
    if units <= 0:
        return False
    month = _cycle(name, now)
    conn.execute("INSERT INTO paid_source_spend (provider, month, units) VALUES (?,?,0) "
                 "ON CONFLICT (provider, month) DO NOTHING", (name, month))
    cur = conn.execute("UPDATE paid_source_spend SET units = units + ? "
                       "WHERE provider=? AND month=? AND units + ? <= ?",
                       (units, name, month, units, cap(name)))
    conn.commit()
    return bool(cur.rowcount and cur.rowcount > 0)


def settle(conn, name: str, reserved: int, used: int, now=None) -> None:
    """Give back the part of a reservation that was not billed."""
    back = max(0, reserved - max(0, used))
    if back:
        conn.execute("UPDATE paid_source_spend SET units = units - ? WHERE provider=? AND month=?",
                     (back, name, _cycle(name, now)))
        conn.commit()


def budget_today(conn, name: str, now=None) -> int:
    """How many units one run may request now: the cycle's remainder spread
    over the days left in it, at most per_run_max."""
    left = cap(name) - spent(conn, name, now)
    if left <= 0:
        return 0
    return max(0, min(SOURCES[name]["per_run_max"], left // _days_left_in_cycle(name, now) or 1, left))


# ── HTTP: one attempt, never a retry ───────────────────────────────────────

class SourceError(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code

    @property
    def billed_maybe(self) -> bool:
        """A 4xx (bad key, bad request, out of credits) returned no jobs and is
        not billed; a timeout, network error or 5xx might have been."""
        return not (self.code and 400 <= self.code < 500)


def _http(method, url, headers=None, body=None, timeout=HTTP_TIMEOUT):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Accept": "application/json", "User-Agent": "JobHunter/1.0 (+https://job-hunter.work)"}
    if data is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except (OSError, ValueError):     # no body to read: the status code is enough
            detail = ""
        raise SourceError("HTTP %s %s" % (e.code, detail.replace("\n", " ")), e.code)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SourceError("%s: %s" % (type(e).__name__, e))
    except ValueError as e:
        raise SourceError("not JSON: %s" % e)


# ── one function per provider: (titles, limit) -> [job dict] ───────────────
# job dict: provider_job_id, title, company, location, url, description, posted_at

def _fetch_theirstack(titles, limit):
    data = _http("POST", "https://api.theirstack.com/v1/jobs/search",
                 headers={"Authorization": "Bearer " + api_key("theirstack")},
                 body={"page": 0, "limit": limit, "job_title_or": titles,
                       "job_country_code_or": ["IL"], "posted_at_max_age_days": 3,
                       "blur_company_data": False, "include_total_results": False})
    out = []
    for j in (data or {}).get("data") or []:
        comp = j.get("company_object") or {}
        out.append({"provider_job_id": str(j.get("id") or ""), "title": j.get("job_title") or "",
                    "company": j.get("company") or comp.get("name") or "",
                    "location": j.get("location") or j.get("short_location") or "Israel",
                    "url": j.get("final_url") or j.get("url") or j.get("source_url") or "",
                    "description": (j.get("description") or "")[:4000],
                    "posted_at": str(j.get("date_posted") or "")[:10]})
    return out


def _fetch_hirebase(titles, limit):
    data = _http("POST", "https://api.hirebase.org/v2/jobs/search",
                 headers={"x-api-key": api_key("hirebase")},
                 body={"job_titles": titles, "geo_locations": [{"country": "Israel"}],
                       "days_ago": 3, "limit": min(limit, 100), "page": 1})
    out = []
    for j in (data or {}).get("jobs") or []:
        locs = j.get("locations") or []
        loc = ", ".join(x for x in ((locs[0] or {}).get("city"), (locs[0] or {}).get("country")) if x) \
            if locs and isinstance(locs[0], dict) else (j.get("location_raw") or "Israel")
        out.append({"provider_job_id": str(j.get("_id") or j.get("job_slug") or ""),
                    "title": j.get("job_title") or j.get("job_title_raw") or "",
                    "company": j.get("company_name") or "", "location": loc or "Israel",
                    "url": j.get("application_link") or j.get("job_board_link") or "",
                    "description": (j.get("description") or "")[:4000],
                    "posted_at": str(j.get("date_posted") or "")[:10]})
    return out


def _quote(t):
    t = t.replace('"', " ").strip()
    return '"%s"' % t if " " in t else t


def _fetch_fantastic(titles, limit):
    qs = urllib.parse.urlencode({"time_frame": "24h", "limit": limit, "location": "Israel",
                                 "title": " OR ".join(_quote(t) for t in titles)})
    data = _http("GET", "https://data.fantastic.jobs/v1/active-ats?" + qs,
                 headers={"Authorization": "Bearer " + api_key("fantastic")})
    rows = data if isinstance(data, list) else (data or {}).get("jobs") or (data or {}).get("data") or []
    out = []
    for j in rows:
        locs = j.get("locations_derived") or []
        out.append({"provider_job_id": str(j.get("id") or ""), "title": j.get("title") or "",
                    "company": j.get("organization") or "",
                    "location": (locs[0] if locs else "Israel") or "Israel",
                    "url": j.get("url") or "", "description": (j.get("description_text") or "")[:4000],
                    "posted_at": str(j.get("date_posted") or "")[:10]})
    return out


def _fetch_apify(titles, limit):
    url = ("https://api.apify.com/v2/acts/%s/run-sync-get-dataset-items?timeout=%d&maxItems=%d"
           % (ETHOSIA_ACTOR, APIFY_TIMEOUT - 20, limit))
    data = _http("POST", url, headers={"Authorization": "Bearer " + api_key("apify")},
                 body={"startUrls": [{"url": "https://ethosia.co.il/job-sitemap.xml"}],
                       "maxItems": limit, "monitorMode": "snapshot",
                       "sortBy": "postedDate", "sortOrder": "desc",
                       "proxyConfiguration": {"useApifyProxy": False}},
                 timeout=APIFY_TIMEOUT)
    out = []
    for j in data if isinstance(data, list) else []:
        out.append({"provider_job_id": str(j.get("stableJobId") or j.get("jobId") or j.get("url") or ""),
                    "title": j.get("title") or "", "company": j.get("companyName") or "",
                    "location": j.get("location") or "Israel",
                    # applyUrl can be the hiring company's own page (The owner's direct-link rule)
                    "url": j.get("applyUrl") or j.get("url") or "",
                    "description": (j.get("description") or "")[:4000],
                    "posted_at": str(j.get("postedDate") or "")[:10]})
    return out


FETCHERS = {"theirstack": _fetch_theirstack, "hirebase": _fetch_hirebase,
            "fantastic": _fetch_fantastic, "apify": _fetch_apify}


# ── the daily run ──────────────────────────────────────────────────────────

# Words that make a title more senior. A title carrying one of these is never
# folded into a shorter title without it (The owner, 2026-11-09: "the senior titles
# are important"), and senior titles get the daily slots first.
SENIORITY_WORDS = {"senior", "sr", "lead", "principal", "staff", "head", "director",
                   "vp", "vice", "president", "chief", "cpo", "cto", "ceo", "coo",
                   "cmo", "group", "executive"}
MIN_ROTATING_SLOTS = 4       # slots kept for non-senior titles when there are any


def _words(title: str) -> frozenset:
    import re
    return frozenset(w for w in re.split(r"[^a-z0-9]+", title.lower()) if w)


def _seniority(words) -> frozenset:
    return frozenset(w for w in words if w in SENIORITY_WORDS)


def _collapse(counts: dict) -> dict:
    """Drop a title when a shorter title's words are all in it AND it adds no
    seniority word: providers match title words in any order, so "Product
    Manager" already returns "AI Product Manager". "Senior Product Manager" is
    kept - it adds "senior". A dropped title's user count goes to the title
    that covers it. {key: (count, title)} -> same shape."""
    items = sorted(counts.items(), key=lambda kv: (len(_words(kv[1][1])), kv[0]))
    kept = {}
    for key, (n, title) in items:
        w = _words(title)
        cover = None
        for k2, (_n2, t2) in kept.items():
            w2 = _words(t2)
            if w2 and w2 < w and _seniority(w) <= _seniority(w2):
                cover = k2
                break
        if cover is None:
            kept[key] = (n, title)
        else:
            kept[cover] = (kept[cover][0] + n, kept[cover][1])
    return kept


def _rotate(items: list, k: int, day: int) -> list:
    """k items from `items`, a different slice each day, wrapping around, so
    every item is asked at least every ceil(len/k) days."""
    if k <= 0 or not items:
        return []
    if len(items) <= k:
        return list(items)
    start = (day * k) % len(items)
    return [items[(start + i) % len(items)] for i in range(k)]


def title_plan(conn, now=None) -> dict:
    """Which titles the pool asks today, and why.
    {titles, total, after_collapse, senior} - counts only go to health."""
    import entitlements
    counts = {}
    rows = conn.execute(
        "SELECT u.*, p.job_titles AS _titles FROM users u JOIN user_profiles p ON p.user_id=u.id "
        "WHERE COALESCE(u.is_active, 1) = 1").fetchall()
    for r in rows:
        u = dict(r)
        if not (entitlements.is_admin(u) or entitlements.limit(u, "paid_sources")):
            continue
        try:
            ts = json.loads(u.get("_titles") or "[]")
        except ValueError:
            ts = [t.strip() for t in str(u.get("_titles") or "").split(",")]
        for t in ts:
            t = " ".join(str(t).split())[:80]
            if t:
                counts[t.lower()] = (counts.get(t.lower(), (0, t))[0] + 1, t)
    kept = _collapse(counts)
    order = lambda x: (-x[0], x[1].lower())
    senior = [t for _n, t in sorted((v for v in kept.values() if _seniority(_words(v[1]))), key=order)]
    others = [t for _n, t in sorted((v for v in kept.values() if not _seniority(_words(v[1]))), key=order)]
    day = (now or _now()).astimezone(_LOCAL).date().toordinal()
    senior_slots = min(len(senior), MAX_TITLES - min(MIN_ROTATING_SLOTS, len(others)))
    picked = _rotate(senior, senior_slots, day)
    picked += _rotate(others, MAX_TITLES - len(picked), day)
    return {"titles": picked, "total": len(counts), "after_collapse": len(kept), "senior": len(senior)}


def pool_titles(conn, now=None) -> list:
    """Today's titles for the paid sources: senior titles first, never folded
    away; the rest folded where a shorter title covers them, then rotated."""
    return title_plan(conn, now)["titles"]


def _record_run(conn, name, day, status, fetched, detail):
    conn.execute(
        "INSERT INTO paid_pool_runs (provider, day, status, fetched, detail, ran_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT (provider, day) DO UPDATE SET status=excluded.status, fetched=excluded.fetched, "
        "detail=excluded.detail, ran_at=excluded.ran_at",
        (name, day, status, fetched, (detail or "")[:300], _now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()


def _store(conn, name, jobs):
    added = 0
    at = _now().strftime("%Y-%m-%d %H:%M:%S")
    for j in jobs:
        if not (j.get("title") and j.get("url")):
            continue
        pid = j.get("provider_job_id") or j["url"]
        cur = conn.execute(
            "INSERT INTO paid_pool_jobs (provider, provider_job_id, title, company, location, url, "
            "description, posted_at, fetched_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (provider, provider_job_id) DO NOTHING",
            (name, pid[:200], j["title"][:300], (j.get("company") or "")[:200],
             (j.get("location") or "")[:200], j["url"][:1000], j.get("description") or "",
             j.get("posted_at") or "", at))
        added += max(cur.rowcount or 0, 0)
    conn.commit()
    return added


def run_source(conn, name, now=None, fetchers=None) -> dict:
    """Run one provider once. Returns {status, fetched, added, detail}.
    status: ok | no_key | no_titles | budget_spent | error."""
    if not api_key(name):
        return {"status": "no_key", "fetched": 0, "added": 0, "detail": "no API key set"}
    plan = title_plan(conn, now)
    titles = plan["titles"]
    if not titles and name != "apify":            # Ethosia is pulled whole, newest first
        return {"status": "no_titles", "fetched": 0, "added": 0, "detail": "no user titles to search"}
    limit = budget_today(conn, name, now)
    if limit <= 0 or not reserve(conn, name, limit, now):
        return {"status": "budget_spent", "fetched": 0, "added": 0,
                "detail": "monthly cap reached (%d of %d)" % (spent(conn, name, now), cap(name))}
    fetch = (fetchers or FETCHERS)[name]
    try:
        jobs = fetch(titles, limit)
    except SourceError as e:
        # A timeout / 5xx may have been billed: keep the whole reservation
        # (over-count, never under-count). A 4xx was not: give it back.
        if not e.billed_maybe:
            settle(conn, name, limit, 0, now)
        return {"status": "error", "fetched": 0, "added": 0, "detail": str(e)}
    except Exception as e:   # a parsing bug - same rule
        return {"status": "error", "fetched": 0, "added": 0, "detail": "%s: %s" % (type(e).__name__, e)}
    settle(conn, name, limit, len(jobs), now)
    added = _store(conn, name, jobs)
    return {"status": "ok", "fetched": len(jobs), "added": added,
            "detail": "requested %d, got %d, %d new to the pool; titles asked %d of %d (%d after folding, %d senior)"
                      % (limit, len(jobs), added, len(titles), plan["total"], plan["after_collapse"], plan["senior"])}


def run_daily(get_db, now=None, fetchers=None) -> dict:
    """Run every provider that has not run today (Israel date), after
    RUN_AFTER_HOUR. Safe to call every minute. Returns {provider: result}
    for the ones that ran."""
    now = now or _now()
    if now.astimezone(_LOCAL).hour < RUN_AFTER_HOUR:
        return {}
    if not _RUN_LOCK.acquire(blocking=False):
        return {}
    try:
        day = _day(now)
        out = {}
        conn = get_db()
        try:
            done = {r[0] for r in conn.execute("SELECT provider FROM paid_pool_runs WHERE day=?", (day,))}
            for name in enabled_sources():
                if name in done:
                    continue
                res = run_source(conn, name, now, fetchers)
                # 'no_key' is not recorded as the day's run, so adding a key
                # later the same day starts the source within a minute.
                if res["status"] != "no_key":
                    _record_run(conn, name, day, res["status"], res["fetched"], res["detail"])
                    print("[pool] %s: %s - %s" % (name, res["status"], res["detail"]))
                out[name] = res
        finally:
            conn.close()
        return out
    finally:
        _RUN_LOCK.release()


# ── what a user's search reads ─────────────────────────────────────────────

def jobs_for_search(conn, now=None) -> list:
    """Every pool job fetched in the last POOL_MAX_AGE_DAYS, as the raw dicts
    the search pipeline takes. The search's own relevance gate, dedup and
    scoring decide what reaches each user."""
    since = ((now or _now()) - timedelta(days=POOL_MAX_AGE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT provider, title, company, location, url, description FROM paid_pool_jobs "
        "WHERE fetched_at >= ? ORDER BY fetched_at DESC", (since,)).fetchall()
    return [{"title": r[1], "company": r[2] or "", "location": r[3] or "", "url": r[4],
             "description": r[5] or "", "source": SOURCE_NAME.get(r[0], r[0])} for r in rows]


def status(conn, now=None) -> dict:
    """For /api/health: per provider, key present, month spend vs cap, last run."""
    out = {}
    for name, s in SOURCES.items():
        last = conn.execute("SELECT day, status, fetched, detail FROM paid_pool_runs WHERE provider=? "
                            "ORDER BY day DESC LIMIT 1", (name,)).fetchone()
        pooled = conn.execute("SELECT COUNT(*) FROM paid_pool_jobs WHERE provider=?", (name,)).fetchone()[0]
        _cs, _cn = _cycle_bounds(name, now)
        out[name] = {"label": s["label"], "enabled": name in enabled_sources(), "key": bool(api_key(name)),
                     "cycle": "%s to %s" % (_cs.isoformat(), _cn.isoformat()),
                     "month_used": spent(conn, name, now), "month_cap": cap(name),
                     "free_allowance": s["hard_limit"], "in_pool": int(pooled or 0),
                     "last_run": ({"day": last[0], "status": last[1], "fetched": last[2], "detail": last[3]}
                                  if last else None)}
    return out
