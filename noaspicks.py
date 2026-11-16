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
    (an application system, or a site whose address names the company - see
    employer_link) - never a job board, directory, aggregator or agency;
  * not known to be closed or dead.
One role per (company, title), newest first, at most two per company so one
employer does not fill the post, at most LIST_MAX (10) listed - and the number the
post states is the number it lists (the owner, 2026-11-16: "22 is too much").

Pure functions plus one read; app.py serves it to the admin only.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import links

LIST_MAX = 10          # roles named in one post
MIN_ROLES = 5          # fewer than this is not a post (the owner, 2026-11-16: "a minimum of 4 or 5")
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


# Recruitment agencies list a client's role on their OWN site, so the link passes the
# employer check below while the "company" is the agency, not the employer. Seen in the first
# production run (2026-11-16): JRG Partners. Extend this as more turn up.
AGENCY_HOSTS = ("jrgpartners.com",)
_AGENCY_WORDS = re.compile(r"\b(recruit\w*|staffing|headhunt\w*|executive search|placement|"
                           r"השמה|גיוס)\b", re.I)


def _letters(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def employer_link(url: str, company: str) -> bool:
    """Does the link go to THIS employer's own page or its application system?

    links.classify() calls every host it does not recognise "company" - the right default for
    the apply engine, too loose for a post that says "each on the company's own careers page":
    the first production run (2026-11-16) listed bayitil.com, a directory of companies, as
    i-BrainTech's page. So a "company" link counts only when its address names the company
    (monday.com for monday.com, careers.google.com for Google, checkpoint.com for Check Point).
    Application systems (Greenhouse, Lever, Comeet, Workday, ...) carry the employer in the
    path and are trusted as links.classify finds them. Agencies are never the employer."""
    kind = links.classify(url)
    if kind not in ("direct_ats", "company"):
        return False
    host = links.host_of(url) or ""
    if any(host == h or host.endswith("." + h) for h in AGENCY_HOSTS) or _AGENCY_WORDS.search(company or ""):
        return False
    if kind == "direct_ats":
        return True
    host_letters = _letters(host)
    name = _letters(company)
    if len(name) >= 3 and name in host_letters:
        return True
    words = [w for w in re.findall(r"[a-z0-9]+", (company or "").lower()) if len(w) >= 3 and w not in ("com", "ltd", "inc", "the", "labs", "group", "technologies", "tech", "systems")]
    return any(w in host_letters for w in words)


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
        if not employer_link(url, company):
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


# The wording around the list. Two voices: "page" is the Job Hunter page speaking as Noa
# (first person; she always says she is an AI), "personal" is the owner's own profile speaking
# ABOUT Noa. Several versions of each, rotated week by week so a group never sees the same
# post twice running. {n} = roles listed, {family} / {family_he} = the role family.
# Approved by the owner 2026-11-16 (doc: "Noa's picks posts - text versions for approval"),
# with one rule for every post: stay positive - it ends "works for you", WITHOUT "not for the
# companies" (employers and recruiters read these groups too). The landing page keeps its line.
VERSIONS = {
    "page": (
        {"key": "A",
         "he_open": "היי, אני נועה. אלה {n} משרות {family_he} שמצאתי השבוע, כל אחת בעמוד הקריירה של החברה עצמה:",
         "he_close": "רוצים שאחפש גם בשבילכם? אני סוכנת AI אישית לחיפוש עבודה: שולחים לי קורות חיים, ואני מביאה כל בוקר "
                     "רק את המשרות שמתאימות לכם. אני עובדת בשבילכם.",
         "en_open": "Hi, I'm Noa. Here are {n} {family} roles I found this week, each on the company's own careers page:",
         "en_close": "Want me to search for you too? I'm a personal AI headhunter agent: send me your CV, and every "
                     "morning I bring you only the roles that fit. I work for you."},
        {"key": "B",
         "he_open": "{n} משרות {family_he} מהשבוע האחרון. עברתי על אלפי מודעות והשארתי רק את אלה שמובילות ישר "
                    "לעמוד הקריירה של החברה:",
         "he_close": "זו הרשימה הכללית. את הרשימה האישית שלכם, לפי קורות החיים, אני מכינה כל בוקר. "
                     "נועה, סוכנת AI אישית לחיפוש עבודה.",
         "en_open": "{n} {family} roles from the past week. I read thousands of postings and kept only the ones that "
                    "lead straight to the company's own careers page:",
         "en_close": "That's the general list. Your personal one, matched to your CV, I prepare every morning. "
                     "Noa, a personal AI headhunter agent."},
        {"key": "C",
         "he_open": "נמאס לגלול בין לוחות דרושים? עשיתי את זה בשבילכם. {n} משרות {family_he} שמצאתי השבוע, "
                    "ישר מאתרי החברות:",
         "he_close": "אני נועה, סוכנת AI אישית לחיפוש עבודה. תנו לי את קורות החיים שלכם, ואביא לכם כל בוקר עד 7 משרות "
                     "שמתאימות בדיוק לכם. אני עובדת בשבילכם.",
         "en_open": "Tired of scrolling job boards? I did it for you. {n} {family} roles I found this week, straight "
                    "from the companies' own sites:",
         "en_close": "I'm Noa, a personal AI headhunter agent. Give me your CV, and every morning I'll bring you up "
                     "to 7 roles that fit you. I work for you."},
    ),
    "personal": (
        {"key": "D",
         "he_open": "בניתי סוכנת AI בשם נועה שמחפשת עבודה במקום מחפשי העבודה. אלה {n} משרות {family_he} שהיא מצאה "
                    "השבוע, כל אחת בעמוד הקריירה של החברה עצמה:",
         "he_close": "רוצים רשימה כזו כל בוקר, מותאמת לקורות החיים שלכם? נועה פתוחה לכולם, וכרגע בחינם. "
                     "היא עובדת בשבילכם.",
         "en_open": "I built an AI agent called Noa that does the job search for the job seeker. Here are {n} "
                    "{family} roles she found this week, each on the company's own careers page:",
         "en_close": "Want a list like this every morning, matched to your CV? Noa is open to everyone, and free "
                     "for now. She works for you."},
        {"key": "E",
         "he_open": "מי שחיפש עבודה יודע כמה שעות הולכות על גלילה בלוחות דרושים. נועה, סוכנת ה-AI שבניתי, עושה את זה "
                    "במקומכם. {n} משרות {family_he} שהיא מצאה השבוע, ישר מאתרי החברות:",
         "he_close": "זו הרשימה הכללית. לכל מי שנרשם היא מכינה רשימה אישית לפי קורות החיים, כל בוקר. מוזמנים לנסות:",
         "en_open": "Anyone who has looked for a job knows how many hours go into scrolling job boards. Noa, the AI "
                    "agent I built, does that part for you. {n} {family} roles she found this week, straight from "
                    "the companies' own sites:",
         "en_close": "That's the general list. For everyone who signs up she prepares a personal one, matched to "
                     "their CV, every morning. You're welcome to try her:"},
    ),
}
# where each voice + language is posted, for the link's source tags
_PLACES = {("page", "he"): ("facebook", "page"), ("page", "en"): ("linkedin", "page"),
           ("personal", "he"): ("facebook", "group"), ("personal", "en"): ("linkedin", "post")}


def write_post(voice: str, lang: str, version: dict, label: str, label_he: str, roles: list,
               base: str, family: str) -> str:
    """One post: the version's opening, the roles, its closing, the sign-up link.
    The number in the text is the number of roles LISTED (at most LIST_MAX)."""
    fill = {"n": len(roles), "family": label, "family_he": label_he}
    sep = " | " if lang == "he" else ", "
    lines = [version[lang + "_open"].format(**fill), ""]
    for r in roles:
        lines += ["\u2022 %s%s%s" % (r["title"], sep, r["company"]), r["url"], ""]
    source, medium = _PLACES[(voice, lang)]
    lines += [version[lang + "_close"].format(**fill), _link(base, source, medium, family)]
    return "\n".join(lines)


def _turn(voice: str, family_index: int, week: int, shift: int = 0) -> dict:
    """Which version this week: it moves one on every week, offset per family so two families
    posted the same day do not open with the same line. `shift` lets the admin take the next one."""
    versions = VERSIONS[voice]
    return versions[(week + family_index + shift) % len(versions)]


MIXED = ("mixed", "tech", "בהייטק")      # key, English word, Hebrew words for the all-families post


def headline(lang: str, n: int, label: str, label_he: str) -> str:
    return ("%d משרות %s" % (n, label_he)) if lang == "he" else ("%d %s roles" % (n, label))


def mix(listed: dict, list_max: int = LIST_MAX) -> list:
    """The all-families post: one role from each family in turn (largest family first),
    at most two per employer overall, until the post is full."""
    queues = [list(v["roles"]) for _k, v in sorted(listed.items(), key=lambda kv: -kv[1]["all"])
              if len(v["roles"]) >= MIN_ROLES]
    if len(queues) < 2:                       # one family is that family's own post
        return []
    out, per, seen = [], {}, set()
    while len(out) < list_max and any(queues):
        for q in queues:
            while q:
                r = q.pop(0)
                c = _norm(r["company"])
                if per.get(c, 0) >= PER_COMPANY or r["url"] in seen:
                    continue
                per[c] = per.get(c, 0) + 1
                seen.add(r["url"])
                out.append(r)
                break
            if len(out) >= list_max:
                break
    return out


def instagram_caption(version: dict, label: str, label_he: str, roles: list) -> str:
    """Hebrew, the page's voice. Instagram does not make links in a caption clickable, so the
    roles are named without addresses and the sign-up is "the link in the bio"."""
    fill = {"n": len(roles), "family": label, "family_he": label_he}
    opening = version["he_open"].format(**fill)
    opening = opening.replace(", כל אחת בעמוד הקריירה של החברה עצמה:", ":").replace(
        " והשארתי רק את אלה שמובילות ישר לעמוד הקריירה של החברה:", ":").replace(", ישר מאתרי החברות:", ":")
    lines = [opening, ""] + ["\u2022 %s | %s" % (r["title"], r["company"]) for r in roles]
    lines += ["", "את המשרות תמצאו באתרי החברות.", version["he_close"].format(**fill),
              "הקישור להרשמה בביו.", "", "#דרושים #הייטק #חיפושעבודה #משרות"]
    return "\n".join(lines)


def _family_out(i: int, key: str, en: str, he: str, roles: list, found: int, base_url: str,
                week: int, shift: int) -> dict:
    posts = []
    for voice in ("page", "personal"):
        v = _turn(voice, i, week, shift)
        for lang in ("he", "en"):
            posts.append({"voice": voice, "lang": lang, "version": v["key"],
                          "text": write_post(voice, lang, v, en, he, roles, base_url, key)})
    page = _turn("page", i, week, shift)
    return {"key": key, "label": en, "label_he": he, "found": found, "listed": len(roles), "roles": roles,
            "headline_en": headline("en", len(roles), en, he), "headline_he": headline("he", len(roles), en, he),
            "posts": posts, "instagram": instagram_caption(page, en, he, roles)}


def build(rows, base_url: str, days: int = 7, week: int = 0, shift: int = 0) -> dict:
    listed, skipped = pick(rows)
    families = []
    for i, (key, en, he, _p) in enumerate(FAMILIES):
        got = listed.get(key)
        if not got or len(got["roles"]) < MIN_ROLES:
            continue
        families.append(_family_out(i, key, en, he, got["roles"], got["all"], base_url, week, shift))
    families.sort(key=lambda f: -f["found"])
    mixed = mix(listed)
    if mixed:
        out = _family_out(len(FAMILIES), MIXED[0], MIXED[1], MIXED[2], mixed,
                          sum(f["found"] for f in families), base_url, week, shift)
        out["label"], out["headline_en"] = "All role families", headline("en", len(mixed), "tech", "")
        families.insert(0, out)
    thin = sorted(({"key": k, "count": v["all"]} for k, v in listed.items() if len(v["roles"]) < MIN_ROLES),
                  key=lambda x: -x["count"])
    return {"days": days, "families": families, "too_few": thin, "skipped": skipped,
            "considered": len(rows), "min_roles": MIN_ROLES, "list_max": LIST_MAX}


def report(conn, base_url: str, days: int = 7, now=None, shift: int = 0) -> dict:
    """Read the last `days` days of found jobs, every account, and build the posts."""
    days = max(1, min(30, int(days or 7)))
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cols = ("title", "company", "url", "location", "found_date", "url_verified", "status_check")
    rows = [{c: r[c] for c in cols} for r in conn.execute(
        "SELECT " + ", ".join(cols) + " FROM jobs "
        "WHERE found_date >= ? AND COALESCE(url, '') != '' "
        "ORDER BY found_date DESC LIMIT 5000", (since,)).fetchall()]
    out = build(rows, base_url, days, week=(now or datetime.now(timezone.utc)).isocalendar()[1], shift=shift)
    out["since"] = since
    return out
