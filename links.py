"""
links.py - what kind of link a job carries, and (Step 1 of EXECUTION_PLAN_NEXT.md)
the one place that turns a job-board link into the company's own posting.

Eran's ask (2026-10-25): every relevant job should open the posting on the
company's own site or ATS, not LinkedIn. Before anything is rewritten, this
module answers the measuring question - how many open jobs carry a board link,
and how many of those the existing resolver can already fix - via
`/api/admin/link-report` (1a).

Link types, in the order a job should prefer them:
    direct_ats  - a posting on Greenhouse, Lever, Ashby, SmartRecruiters,
                  Comeet or Workday. The apply engine can submit to these.
    company     - any other host that is not a known board or aggregator:
                  usually the company's own careers site.
    aggregator  - a site that re-publishes postings it does not own
                  (builtin, jobright, secrethunter, jobgether...). The 10-12
                  link-check rebuild found these answer 200 long after the
                  posting closed, so they are not trusted as direct.
    board       - LinkedIn, Indeed, Glassdoor and the Israeli boards: the
                  listing lives there, the application usually does not.
    redirect    - a Gemini grounding redirect; resolves to something else
                  and 404s within days.
    none        - no URL at all.
"""
import urllib.parse

# Kept in step with apply_engine._ATS_DIRECT_HOSTS - that list decides what the
# engine will submit to, so "direct" must never mean more than it does there.
DIRECT_ATS_HOSTS = (
    "boards.greenhouse.io", "job-boards.greenhouse.io", "eu.greenhouse.io",
    "jobs.lever.co", "jobs.ashbyhq.com", "jobs.smartrecruiters.com",
    "comeet.co", "comeet.com", "myworkdayjobs.com",
)

# Same set apply_engine._is_job_board uses.
BOARD_DOMAINS = (
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com",
    "monster.com", "careerjet.com", "simplyhired.com", "dice.com",
    "angel.co", "wellfound.com", "otta.com", "remoteok.com",
    "weworkremotely.com", "remotive.io", "geektime.co.il", "alljobs.co.il",
    "jobmaster.co.il", "drushim.co.il", "jobnet.co.il", "gotfriends.co.il",
)

# Re-publishers observed in Eran's queue (STATUS.md 2026-10-12, and the
# Jobgether row that is still /api/health's last_apply).
AGGREGATOR_DOMAINS = (
    "builtin.com", "builtintelaviv.com", "jobright.ai", "secrethunter.co",
    "secrethunter.com", "jobgether.com", "jooble.org", "talent.com",
    "adzuna.com", "adzuna.co.il", "himalayas.app", "workingnomads.com",
    "jobleads.com", "efinancialcareers.com", "lensa.com",
)

REDIRECT_MARKERS = ("vertexaisearch.cloud.google.com/grounding-api-redirect",)

ORDER = ("direct_ats", "company", "aggregator", "board", "redirect", "none")


def host_of(url: str) -> str:
    try:
        host = urllib.parse.urlparse((url or "").strip()).netloc.lower()
    except Exception:
        return ""
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _on(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def classify(url: str) -> str:
    """One of ORDER. Never raises."""
    u = (url or "").strip()
    if not u:
        return "none"
    low = u.lower()
    if any(m in low for m in REDIRECT_MARKERS):
        return "redirect"
    host = host_of(u)
    if not host:
        return "none"
    if any(h in host for h in DIRECT_ATS_HOSTS):
        return "direct_ats"
    if _on(host, BOARD_DOMAINS):
        return "board"
    if _on(host, AGGREGATOR_DOMAINS):
        return "aggregator"
    return "company"


def needs_resolving(url: str) -> bool:
    """Would a direct link be better than what this job carries now?"""
    return classify(url) in ("board", "aggregator", "redirect", "none")


# ── 1a: the measuring report ────────────────────────────────────────────────

OPEN_STATUSES = ("new", "approved")


def link_report(conn, user_id=None, sample: int = 25, budget_s: float = 45.0,
                use_llm: bool = False, resolver=None) -> dict:
    """What the open queue links to, and how much of it the existing resolver
    could fix. Read-only: nothing is written, no job's URL changes.

    user_id=None reports across every user. `resolver(company, title,
    location, use_llm)` defaults to apply_engine.resolve_ats_application; it is
    called for at most `sample` distinct (company, title) pairs needing a better
    link, and stops early once `budget_s` is spent so the request stays inside
    an HTTP timeout - the report says how many it did not reach.
    """
    import time

    if resolver is None:
        import apply_engine as _ae

        def resolver(company, title, location, use_llm):
            return _ae.resolve_ats_application(company, title, location=location, use_llm=use_llm)

    sql = ("SELECT id, user_id, title, company, location, url, source, status FROM jobs "
           "WHERE status IN ('new','approved')")
    args = ()
    if user_id is not None:
        sql += " AND user_id=?"
        args = (user_id,)
    sql += " ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END, id DESC"
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]

    by_type = {t: 0 for t in ORDER}
    by_host: dict = {}
    by_source: dict = {}
    for r in rows:
        t = classify(r.get("url"))
        r["_type"] = t
        by_type[t] += 1
        h = host_of(r.get("url")) or "(none)"
        by_host.setdefault(h, {"type": t, "jobs": 0})["jobs"] += 1
        src = (r.get("source") or "(unknown)").strip() or "(unknown)"
        by_source.setdefault(src, {t2: 0 for t2 in ORDER})[t] += 1

    # Distinct (company, title) pairs that would benefit from a direct link.
    pairs, seen = [], set()
    for r in rows:
        if r["_type"] in ("direct_ats", "company"):
            continue
        key = ((r.get("company") or "").strip().lower(), (r.get("title") or "").strip().lower())
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        pairs.append(r)

    started = time.monotonic()
    tried, resolved, unresolved, by_ats = 0, [], [], {}
    for r in pairs[:max(0, sample)]:
        if time.monotonic() - started > budget_s:
            break
        tried += 1
        try:
            hit = resolver(r.get("company") or "", r.get("title") or "", r.get("location") or "", use_llm)
        except Exception as exc:  # one bad company must not sink the report
            hit = {"error": f"{type(exc).__name__}: {exc}"[:160]}
        if hit and hit.get("url"):
            by_ats[hit.get("ats") or "?"] = by_ats.get(hit.get("ats") or "?", 0) + 1
            resolved.append({"job_id": r["id"], "company": r.get("company"), "title": r.get("title"),
                             "from": host_of(r.get("url")), "to": hit["url"], "ats": hit.get("ats"),
                             "matched_title": hit.get("matched_title"), "score": hit.get("score")})
        else:
            unresolved.append({"job_id": r["id"], "company": r.get("company"), "title": r.get("title"),
                               "from": host_of(r.get("url")),
                               **({"error": hit["error"]} if hit and hit.get("error") else {})})

    open_total = len(rows)
    needing = sum(by_type[t] for t in ("board", "aggregator", "redirect", "none"))
    return {
        "scope": "all users" if user_id is None else f"user {user_id}",
        "open_jobs": open_total,
        "by_type": by_type,
        "direct_share": round(100.0 * (by_type["direct_ats"] + by_type["company"]) / open_total, 1) if open_total else None,
        "needing_a_direct_link": needing,
        "top_hosts": sorted(({"host": h, **v} for h, v in by_host.items()),
                            key=lambda x: -x["jobs"])[:20],
        "by_source": by_source,
        "dry_run": {
            "use_llm": bool(use_llm),
            "distinct_pairs_needing_link": len(pairs),
            "tried": tried,
            "not_reached": max(0, min(sample, len(pairs)) - tried),
            "resolved": len(resolved),
            "hit_rate": round(100.0 * len(resolved) / tried, 1) if tried else None,
            "by_ats": by_ats,
            "seconds": round(time.monotonic() - started, 1),
            "resolved_examples": resolved[:20],
            "unresolved": unresolved[:40],
        },
    }
