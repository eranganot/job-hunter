"""
noaspicks.py - "Noa's picks": this week's new roles per role family, written as
ready-to-post text in Hebrew and English (GTM Stage 2 row 9, 2026-11-16).

The free channel the launch leans on is posts in job-seeker groups and on
LinkedIn. A promotional post is deleted there; a list of real openings with
links to the employers' own pages is welcome. This module builds that list
from what the engine already found - across every account, since a job
posting is public and nothing about any user goes into the text.

What goes in, so the post makes no claim it cannot back:
  * found in the last `days` days ("Noa found this week" - found, not posted:
    the app knows when it found a role, not when the employer published it);
  * a company name (about a fifth of stored jobs have none - left out);
  * a link to the employer's own page or its application system
    (links.classify: direct_ats / company) - never a job board or aggregator;
  * not known to be closed or dead.
One role per (company, title), newest first, at most two per company so one
employer does not fill the post, at most LIST_MAX listed.

Pure functions plus one read; app.py serves it to the admin only.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import links

LIST_MAX = 10          # roles named in one post
MIN_ROLES = 3          # fewer than this is not a post
PER_COMPANY = 2

# (key, English label, Hebrew label, title patterns). ORDER MATTERS: the first family whose
# pattern matches wins, so "Product Designer" is design, "Product Marketing Manager" is
# marketing, "Data Engineer" is data and "QA Automation Engineer" is QA - not product / software.
FAMILIES = (
    ("design", "Product Design & UX", "עיצוב מוצר ו-UX",
     r"\b(product designer|ux|ui/ux|ux/ui|ui designer|user experience|interaction designer|design lead|head of design)\b"),
    ("marketing", "Marketing", "שיווק",
     r"\b(marketing|growth manager|growth marketer|content manager|content writer|seo|ppc|performance manager|"
     r"brand manager|social media|demand generation|user acquisition)\b"),
    ("data", "Data & Analytics", "דאטה ואנליטיקה",
     r"\b(data analyst|data scientist|data engineer|data science|analytics|bi developer|bi analyst|"
     r"business intelligence|machine learning|ml engineer|ai engineer|research scientist)\b"),
    ("qa", "QA & Automation", "QA ואוטומציה",
     r"\b(qa|quality assurance|test engineer|tester|sdet|automation engineer|test automation)\b"),
    ("devops", "DevOps & Cloud", "DevOps וענן",
     r"\b(devops|sre|site reliability|platform engineer|cloud engineer|infrastructure engineer|devsecops)\b"),
    ("product", "Product Management", "ניהול מוצר",
     r"\b(product manager|product owner|product lead|head of product|vp product|vp of product|director of product|"
     r"chief product|cpo|product management|group product)\b"),
    ("software", "Software Engineering", "פיתוח תוכנה",
     r"\b(software engineer|software developer|developer|backend|back-end|back end|frontend|front-end|front end|"
     r"full ?stack|fullstack|mobile engineer|ios|android|engineering manager|tech lead|team lead|programmer|"
     r"architect|r&d)\b"),
    ("customer", "Sales & Customer Success", "מכירות והצלחת לקוחות",
     r"\b(customer success|account manager|account executive|sales|sdr|bdr|business development|"
     r"customer support|support engineer|solutions engineer|solution engineer|pre-?sales)\b"),
)
_COMPILED = tuple((k, en, he, re.compile(p, re.I)) for k, en, he, p in FAMILIES)


def family_of(title: str):
    """(key, English label, Hebrew label) of the first family the title matches, or None."""
    t = " ".join((title or "").split())
    for key, en, he, rx in _COMPILED:
        if rx.search(t):
            return key, en, he
    return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9֐-׿]+", " ", (s or "").lower()).strip()


def _url_key(url: str) -> str:
    p = urlsplit((url or "").strip())
    return (p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")).lower()


def pick(rows, list_max: int = LIST_MAX, per_company: int = PER_COMPANY):
    """rows: dicts with title, company, url, found_date (and optional location, url_verified,
    status_check). Returns ({family key: [role, ...] newest first}, skipped counts)."""
    skipped = {"no_company": 0, "not_employer_link": 0, "closed": 0, "no_family": 0, "duplicate": 0}
    seen_role, seen_url, out = set(), set(), {}
    for r in sorted(rows, key=lambda x: str(x.get("found_date") or ""), reverse=True):
        title = " ".join(str(r.get("title") or "").split())
        company = " ".join(str(r.get("company") or "").split())
        url = str(r.get("url") or "").strip()
        if not title or len(title) > 90:
            skipped["no_family"] += 1
            continue
        if not company or _norm(company) in ("", "unknown", "n a", "confidential", "company"):
            skipped["no_company"] += 1
            continue
        if links.classify(url) not in ("direct_ats", "company"):
            skipped["not_employer_link"] += 1
            continue
        if r.get("url_verified") == 0 or str(r.get("status_check") or "").lower() == "closed":
            skipped["closed"] += 1
            continue
        fam = family_of(title)
        if not fam:
            skipped["no_family"] += 1
            continue
        role_key, url_key = (_norm(company), _norm(title)), _url_key(url)
        if role_key in seen_role or url_key in seen_url:
            skipped["duplicate"] += 1
            continue
        seen_role.add(role_key)
        seen_url.add(url_key)
        out.setdefault(fam[0], []).append({"title": title, "company": company, "url": url,
                                           "location": " ".join(str(r.get("location") or "").split()),
                                           "found": str(r.get("found_date") or "")[:10]})
    listed = {}
    for key, roles in out.items():
        per, keep = {}, []
        for role in roles:                                   # newest first; cap each employer
            c = _norm(role["company"])
            if per.get(c, 0) >= per_company:
                continue
            per[c] = per.get(c, 0) + 1
            keep.append(role)
        listed[key] = {"all": len(roles), "roles": keep[:list_max]}
    return listed, skipped


def _link(base: str, source: str, medium: str, family: str) -> str:
    return base.rstrip("/") + "/?" + urlencode({"utm_source": source, "utm_medium": medium,
                                                "utm_campaign": "noas_picks", "utm_content": family})


def post_en(label: str, total: int, roles: list, base: str, family: str) -> str:
    """LinkedIn / English groups. `total` is how many roles the week had; `roles` the ones named."""
    lines = ["%d %s roles Noa found this week, each on the company's own careers page:" % (total, label), ""]
    for r in roles:
        lines += ["• %s, %s" % (r["title"], r["company"]), r["url"], ""]
    more = total - len(roles)
    if more > 0:
        lines += ["And %d more this week." % more, ""]
    lines += ["Want a list like this every morning, matched to your CV? Meet Noa, your personal AI headhunter "
              "agent. She works for you, not for the companies.",
              _link(base, "linkedin", "post", family)]
    return "\n".join(lines)


def post_he(label_he: str, total: int, roles: list, base: str, family: str) -> str:
    """Hebrew Facebook groups. Job titles and company names stay as the employer wrote them."""
    lines = ["%d משרות %s שנועה מצאה השבוע, כל אחת בעמוד הקריירה של החברה עצמה:" % (total, label_he), ""]
    for r in roles:
        lines += ["• %s | %s" % (r["title"], r["company"]), r["url"], ""]
    more = total - len(roles)
    if more > 0:
        lines += ["ועוד %d משרות השבוע." % more, ""]
    lines += ["רוצים רשימה כזו כל בוקר, מותאמת לקורות החיים שלכם? הכירו את נועה, סוכנת ה-AI האישית שלכם "
              "לחיפוש עבודה. היא עובדת בשבילכם, לא בשביל החברות.",
              _link(base, "facebook", "group", family)]
    return "\n".join(lines)


def build(rows, base_url: str, days: int = 7) -> dict:
    listed, skipped = pick(rows)
    families = []
    for key, en, he, _p in FAMILIES:
        got = listed.get(key)
        if not got or len(got["roles"]) < MIN_ROLES:
            continue
        families.append({"key": key, "label": en, "label_he": he, "count": got["all"],
                         "listed": len(got["roles"]), "roles": got["roles"],
                         "post_en": post_en(en, got["all"], got["roles"], base_url, key),
                         "post_he": post_he(he, got["all"], got["roles"], base_url, key)})
    families.sort(key=lambda f: -f["count"])
    thin = sorted(({"key": k, "count": v["all"]} for k, v in listed.items() if len(v["roles"]) < MIN_ROLES),
                  key=lambda x: -x["count"])
    return {"days": days, "families": families, "too_few": thin, "skipped": skipped,
            "considered": len(rows), "min_roles": MIN_ROLES}


def report(conn, base_url: str, days: int = 7, now=None) -> dict:
    """Read the last `days` days of found jobs, every account, and build the posts."""
    days = max(1, min(30, int(days or 7)))
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cols = ("title", "company", "url", "location", "found_date", "url_verified", "status_check")
    rows = [{c: r[c] for c in cols} for r in conn.execute(
        "SELECT " + ", ".join(cols) + " FROM jobs "
        "WHERE found_date >= ? AND COALESCE(url, '') != '' "
        "ORDER BY found_date DESC LIMIT 5000", (since,)).fetchall()]
    out = build(rows, base_url, days)
    out["since"] = since
    return out
