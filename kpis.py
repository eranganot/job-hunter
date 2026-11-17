"""
kpis.py - the launch numbers on Admin -> Today and Growth (GTM row 10a part 2).

Definitions are the ones the owner approved in "Admin page rebuild - structure
and KPIs for approval" (2026-11-16). Every number covers people only: admins
and the resettable test account are left out. Each Today number is for the
last 7 days with the 7 days before beside it.

Where each comes from (all already recorded; nothing here writes):
  * a decision (swipe) = activity_log job_approved / job_rejected, or
    bulk_approve / bulk_reject, whose details carry the count ("Approved 5 job(s)");
  * a real application = jobs.status 'applied' with applied_via 'engine' (the
    apply engine submitted it) or 'manual' (the user marked that one job
    applied). 'bulk' (queue clean-up) and 'no_url' are not applications;
  * sign-up, first search, first approve = funnel_events;
  * first real application = the user's earliest such job (row 12, 2026-11-17);
  * new matches a day = jobs found per person per day (jobs.found_date), before
    the daily cap holds any back - with the share of days under the cap;
  * where a sign-up came from = users.signup_attr (utm_* / ref / noa variant,
    saved at sign-up since migration 38; earlier accounts read "before tracking").
"""
import json
import re
import statistics
from datetime import datetime, timedelta, timezone

DECIDE = {"job_approved": ("approved", None), "job_rejected": ("passed", None),
          "bulk_approve": ("approved", "n"), "bulk_reject": ("passed", "n")}
REAL_VIA = ("engine", "manual")
_N = re.compile(r"(\d+)")
FMT = "%Y-%m-%d %H:%M:%S"


def _ts(dt):
    return dt.strftime(FMT)


def _parse(s):
    s = str(s or "").replace("T", " ")[:19]
    try:
        return datetime.strptime(s, FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def people(conn, test_email):
    """ids of real users: not admin, not the test account, not disabled by the admin."""
    return {int(r["id"]) for r in conn.execute(
        "SELECT id, role, email, COALESCE(is_active, 1) AS on_ FROM users").fetchall()
        if str(r["role"] or "") != "admin" and str(r["email"] or "").strip().lower() != test_email
        and int(r["on_"] or 0) == 1}


def _decisions(conn, ids, start, end):
    out = {}
    marks = ",".join("?" for _ in DECIDE)
    for r in conn.execute(
            "SELECT user_id, event_type, details, created_date FROM activity_log "
            "WHERE event_type IN (%s) AND created_date >= ? AND created_date < ?" % marks,
            (*DECIDE, _ts(start), _ts(end))).fetchall():
        uid = int(r["user_id"])
        if uid not in ids:
            continue
        kind, counted = DECIDE[r["event_type"]]
        n = 1
        if counted:
            m = _N.search(str(r["details"] or ""))
            n = int(m.group(1)) if m else 0
        d = out.setdefault(uid, {"approved": 0, "passed": 0, "times": []})
        d[kind] += n
        d["times"].append(_parse(r["created_date"]))
    return out


def _applications(conn, ids, start, end):
    out = {}
    for r in conn.execute(
            "SELECT user_id, COUNT(*) AS n FROM jobs WHERE status='applied' AND applied_via IN (?,?) "
            "AND SUBSTR(applied_date,1,10) >= ? AND SUBSTR(applied_date,1,10) < ? GROUP BY user_id",
            (*REAL_VIA, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))).fetchall():
        if int(r["user_id"]) in ids:
            out[int(r["user_id"])] = int(r["n"])
    return out


def _steps(conn, ids):
    """user -> {step: datetime} from funnel_events."""
    out = {}
    for r in conn.execute("SELECT user_id, step, created_at FROM funnel_events").fetchall():
        if int(r["user_id"]) in ids:
            out.setdefault(int(r["user_id"]), {})[r["step"]] = _parse(r["created_at"])
    return out


def _first_applications(conn, ids, steps, start, end):
    """People whose FIRST real application (engine or manual, never bulk) fell in
    [start, end), and the median days from their sign-up to it."""
    firsts = {}
    for r in conn.execute("SELECT user_id, MIN(applied_date) AS first FROM jobs WHERE status='applied' "
                          "AND applied_via IN (?,?) AND applied_date IS NOT NULL GROUP BY user_id", REAL_VIA).fetchall():
        uid = int(r["user_id"])
        t = _parse(r["first"])
        if uid in ids and t and start <= t < end:
            firsts[uid] = t
    days = [(t - steps[u]["signup"]).total_seconds() / 86400 for u, t in firsts.items()
            if (steps.get(u) or {}).get("signup") and t >= steps[u]["signup"]]
    return len(firsts), (round(statistics.median(days), 1) if days else None)


def _new_matches(conn, ids, start, end, cap):
    """Matches the searches found per person per day (jobs.found_date), before the
    daily cap holds any back: the average over person-days with at least one,
    and the share of those days that came in under the cap (supply too thin)."""
    per = {}
    for r in conn.execute("SELECT user_id, SUBSTR(found_date,1,10) AS d, COUNT(*) AS n FROM jobs "
                          "WHERE found_date >= ? AND found_date < ? GROUP BY user_id, SUBSTR(found_date,1,10)",
                          (_ts(start), _ts(end))).fetchall():
        if int(r["user_id"]) in ids:
            per[(int(r["user_id"]), r["d"])] = int(r["n"])
    if not per:
        return None, None, 0
    vals = list(per.values())
    under = sum(1 for n in vals if n < cap)
    return round(statistics.mean(vals), 1), _pct(under, len(vals)), len(vals)


def _pct(a, b):
    return round(100.0 * a / b) if b else None


def week(conn, ids, steps, start, end, now, cap=7):
    dec = _decisions(conn, ids, start, end)
    apps = _applications(conn, ids, start + timedelta(days=1), end + timedelta(days=1))
    swipers = [d for d in dec.values() if d["approved"] + d["passed"]]
    rates = [d["approved"] / (d["approved"] + d["passed"]) for d in swipers]
    approvers = {u: d["approved"] for u, d in dec.items() if d["approved"]}
    applied_rates = [min(apps.get(u, 0) / n, 1.0) for u, n in approvers.items()]

    signed = {u: s for u, s in steps.items() if s.get("signup") and start <= s["signup"] < end}
    ripe = {u: s for u, s in signed.items() if now - s["signup"] >= timedelta(hours=48)}
    activated = [u for u, s in ripe.items()
                 if s.get("first_approve") and s["first_approve"] - s["signup"] <= timedelta(hours=48)]
    waits = [(s["first_search"] - s["signup"]).total_seconds() / 60 for s in signed.values()
             if s.get("first_search") and s["first_search"] >= s["signup"]]

    # Day-7 return: sign-ups of the week that ended 8 days before `end`, so the
    # 6-to-8-day window has fully passed for every one of them.
    c_start, c_end = start - timedelta(days=8), end - timedelta(days=8)
    cohort = {u: s["signup"] for u, s in steps.items() if s.get("signup") and c_start <= s["signup"] < c_end}
    back = 0
    if cohort:
        later = _decisions(conn, set(cohort), c_start + timedelta(days=6), end)
        for u, t0 in cohort.items():
            times = (later.get(u) or {}).get("times") or []
            if any(t and t0 + timedelta(days=6) <= t <= t0 + timedelta(days=8) for t in times):
                back += 1
    first_n, first_days = _first_applications(conn, ids, steps, start, end)
    per_day, under_cap, person_days = _new_matches(conn, ids, start, end, cap)
    return {
        "active_applicants": {"value": sum(1 for n in apps.values() if n)},
        "first_applications": {"value": first_n, "median_days": first_days},
        "new_matches_per_day": {"value": per_day, "under_cap_pct": under_cap, "of": person_days, "cap": cap},
        "weekly_active_swipers": {"value": len(swipers)},
        "approve_rate": {"value": round(100 * statistics.mean(rates)) if rates else None, "of": len(rates)},
        "applied_rate": {"value": round(100 * statistics.mean(applied_rates)) if applied_rates else None,
                         "of": len(applied_rates)},
        "activation_48h": {"value": _pct(len(activated), len(ripe)), "n": len(activated), "of": len(ripe)},
        "time_to_first_picks": {"value": round(statistics.median(waits)) if waits else None, "of": len(waits)},
        "day7_return": {"value": _pct(back, len(cohort)), "n": back, "of": len(cohort)},
    }


def today(conn, test_email, now=None, cap=7):
    now = now or datetime.now(timezone.utc)
    ids = people(conn, test_email)
    steps = _steps(conn, ids)
    cur = week(conn, ids, steps, now - timedelta(days=7), now, now, cap)
    prev = week(conn, ids, steps, now - timedelta(days=14), now - timedelta(days=7), now, cap)
    for k, v in cur.items():
        v["prev"] = prev[k]["value"]
    return {"since": _ts(now - timedelta(days=7)), "kpis": cur}


def _source(attr):
    if not attr:
        return "before tracking"
    if attr.get("utm_source"):
        return attr["utm_source"] + (" / " + attr["utm_medium"] if attr.get("utm_medium") else "")
    if attr.get("ref"):
        return "referral"
    return "direct"


def by_source(conn, test_email, days=30, now=None):
    """Sign-ups, activation and active applicants per channel and per Noa variant."""
    now = now or datetime.now(timezone.utc)
    ids = people(conn, test_email)
    steps = _steps(conn, ids)
    since = now - timedelta(days=days)
    apps = _applications(conn, ids, now - timedelta(days=6), now + timedelta(days=1))
    attrs = {}
    for r in conn.execute("SELECT id, signup_attr FROM users").fetchall():
        try:
            attrs[int(r["id"])] = json.loads(r["signup_attr"]) if r["signup_attr"] else None
        except (ValueError, TypeError):
            attrs[int(r["id"])] = None
    rows, variants = {}, {}
    for u, s in steps.items():
        if not s.get("signup") or s["signup"] < since:
            continue
        a = attrs.get(u)
        for table, key in ((rows, _source(a)), (variants, (a or {}).get("noa") or ("before tracking" if not a else "none"))):
            t = table.setdefault(key, {"signups": 0, "activated": 0, "applicants": 0})
            t["signups"] += 1
            t["activated"] += 1 if s.get("first_approve") else 0
            t["applicants"] += 1 if apps.get(u) else 0
    order = lambda d: [dict(v, name=k) for k, v in sorted(d.items(), key=lambda kv: -kv[1]["signups"])]
    return {"days": days, "sources": order(rows), "noa_variants": order(variants)}
