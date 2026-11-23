"""probes.py - three accounts that search every day for the role families no user covers yet
(GTM Stage 2 row 17g, 2026-11-22).

Noa's picks (noaspicks.py) lists what the engine found across every account, and a role family
gets a post only with MIN_ROLES (5) postable roles in a week. Before Day 1 the users are mostly
product people, so QA, design and sales / customer success were barely searched for at all.
These three accounts carry a profile for each of those families and search daily until Day 1
(JH_PROBES_UNTIL, default 2026-12-01, Israel time), so:
  * the week-1 posts for those families have roles to list, and
  * the admin sees new roles a day per family (supply()) - the supply check of row 25, early.

- Addresses are on the reserved .test domain, like the resettable test account: mail to them
  is refused (app.send_email), testaccount.is_test_address() is true for them, so they are not
  people in any number, get no lifecycle mail, are never paused for inactivity, never reach
  analytics or Meta, and raise no "new user" alert.
- Created here, not through /register: no signup funnel event.
- From JH_PROBES_UNTIL they are switched off (users.is_active = 0): the scheduler only searches
  active accounts, so the Gemini spend stops. Their jobs stay and keep feeding Noa's picks for
  the week the picks look back. Moving the date later turns them on again.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import date, datetime, timedelta, timezone

DOMAIN = "@job-hunter.test"
SEARCH_HOUR = 6          # Israel time: before the users' searches, so the worker is free

# key, the noaspicks family it feeds, name, job titles, keywords, the CV summary the scorer reads
PROBES = (
    ("qa", "qa", "Probe QA",
     ["QA Engineer", "QA Automation Engineer", "Senior QA Engineer", "Test Automation Engineer", "SDET"],
     ["QA", "test automation", "Selenium", "Playwright", "Cypress", "Python", "API testing", "CI/CD"],
     "QA automation engineer with 6 years in Israeli SaaS companies. Builds and maintains automation "
     "frameworks (Playwright, Selenium, Cypress) in Python and TypeScript, API and end-to-end tests in CI/CD, "
     "test planning for web and mobile, works closely with developers and product."),
    ("design", "design", "Probe Design",
     ["Product Designer", "UX Designer", "UI/UX Designer", "Senior Product Designer", "UX Researcher"],
     ["product design", "UX", "UI", "Figma", "design systems", "user research", "prototyping", "interaction design"],
     "Product designer with 6 years designing B2B and B2C web and mobile products in Israeli startups. "
     "End-to-end UX and UI in Figma, design systems, user research and usability testing, prototyping, "
     "works with product managers and developers from discovery to release."),
    ("customer", "customer", "Probe Sales and CS",
     ["Customer Success Manager", "Account Manager", "Account Executive", "Sales Development Representative",
      "Solutions Engineer"],
     ["customer success", "account management", "SaaS", "B2B sales", "onboarding", "renewals", "Salesforce",
      "HubSpot"],
     "Customer success and account manager with 6 years in B2B SaaS companies in Israel. Owns a book of "
     "enterprise accounts, onboarding, renewals and expansion, works with sales on new business, "
     "Salesforce and HubSpot, English at mother-tongue level."),
)


def email(key: str) -> str:
    return "probe-%s%s" % (key, DOMAIN)


def is_probe(addr) -> bool:
    a = str(addr or "").strip().lower()
    return a.startswith("probe-") and a.endswith(DOMAIN)


def until() -> date:
    raw = os.environ.get("JH_PROBES_UNTIL", "").strip() or "2026-12-01"
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return date(2026, 12, 1)


def ensure(get_db, create_user, mark_verified, today: date) -> dict:
    """Create the probes that are missing, keep their profiles as written above, and switch
    them on before until() and off from it. `today` is the Israel date. Idempotent."""
    on = 1 if today < until() else 0
    out = {"created": [], "active": bool(on)}
    for key, _fam, name, titles, keywords, summary in PROBES:
        addr = email(key)
        conn = get_db()
        try:
            row = conn.execute("SELECT id, role FROM users WHERE LOWER(email)=?", (addr,)).fetchone()
        finally:
            conn.close()
        if not row:
            if not on:
                continue                             # never create one only to switch it off
            uid, err = create_user(name, addr, secrets.token_urlsafe(16))
            if err:
                print("[probes] could not create %s: %s" % (addr, err))
                continue
            mark_verified(uid)
            out["created"].append(key)
        else:
            if str(row["role"] or "").lower() == "admin":
                continue                             # never touch an admin, whatever its address
            uid = row["id"]
        conn = get_db()
        try:
            conn.execute(
                "UPDATE user_profiles SET job_titles=?, keywords=?, locations=?, cv_summary=?, cv_analyzed=1, "
                "schedule_frequency='daily', search_hour=?, weekdays_only=0, onboarding_complete=1, "
                "notification_channel='none', experience_years=6 WHERE user_id=?",
                (json.dumps(titles), json.dumps(keywords), json.dumps(["Tel Aviv", "Israel"]), summary,
                 SEARCH_HOUR, uid))
            conn.execute("UPDATE users SET is_active=?, search_paused_at=NULL WHERE id=?", (on, uid))
            conn.commit()
        finally:
            conn.close()
    return out


def _day(s) -> str:
    return str(s or "").replace("T", " ")[:10]


def supply(conn, days: int = 14, now: datetime | None = None) -> dict:
    """New roles a day per role family, every account: `found` = distinct company + title in the
    family; `postable` = those Noa's picks could list (a company name, the employer's own page,
    not closed - noaspicks.pick). Plus each probe's state."""
    import noaspicks
    now = now or datetime.now(timezone.utc)
    days = max(1, min(30, int(days or 14)))
    first = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    cols = ("title", "company", "url", "location", "found_date", "url_verified", "status_check")
    by_day: dict = {}
    for r in conn.execute("SELECT " + ", ".join(cols) + " FROM jobs WHERE found_date >= ?", (first,)).fetchall():
        by_day.setdefault(_day(r["found_date"]), []).append({c: r[c] for c in cols})
    out_days = []
    for i in range(days):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        rows = by_day.get(d, [])
        found: dict = {}
        seen = set()
        for r in rows:
            fam = noaspicks.family_of(r["title"])
            k = (noaspicks._norm(r["company"]), noaspicks._norm(r["title"]))
            if not fam or k in seen:
                continue
            seen.add(k)
            found[fam[0]] = found.get(fam[0], 0) + 1
        listed, _skipped = noaspicks.pick([r for r in rows if r.get("url")], list_max=10 ** 6, per_company=10 ** 6)
        out_days.append({"day": d, "families": {
            key: {"found": found.get(key, 0), "postable": listed.get(key, {}).get("all", 0)}
            for key, _en, _he, _p in noaspicks.FAMILIES}})
    probes = []
    for key, fam, name, titles, _k, _s in PROBES:
        r = conn.execute("SELECT id, COALESCE(is_active, 1) AS on_ FROM users WHERE LOWER(email)=?",
                         (email(key),)).fetchone()
        item = {"key": key, "family": fam, "email": email(key), "titles": titles, "exists": bool(r)}
        if r:
            last = conn.execute("SELECT MAX(created_date) FROM activity_log WHERE user_id=? AND event_type='jobs_searched'",
                                (r["id"],)).fetchone()
            n = conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=?", (r["id"],)).fetchone()
            item.update({"active": bool(int(r["on_"] or 0)), "last_search": (last[0] if last else None),
                         "jobs": int(n[0] if n else 0)})
        probes.append(item)
    return {"days": out_days, "families": [{"key": k, "label": en} for k, en, _he, _p in noaspicks.FAMILIES],
            "probes": probes, "until": until().isoformat(), "search_hour": SEARCH_HOUR,
            "min_roles": noaspicks.MIN_ROLES}
