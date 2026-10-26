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
    # Added 2026-10-25 from the link audit of the open queue:
    "djinni.co",            # a board; one job's id opened a different (QA) posting
    "jobify360.co.il",      # Israeli board re-listing other companies' jobs
    "app.greenhouse.io",    # Greenhouse's RECRUITER app: /jobs/<id> lands on sign-in
)

# Re-publishers observed in Eran's queue (STATUS.md 2026-10-12, and the
# Jobgether row that is still /api/health's last_apply).
AGGREGATOR_DOMAINS = (
    "builtin.com", "builtintelaviv.com", "jobright.ai", "secrethunter.co",
    "secrethunter.com", "jobgether.com", "jooble.org", "talent.com",
    "adzuna.com", "adzuna.co.il", "himalayas.app", "workingnomads.com",
    "jobleads.com", "efinancialcareers.com", "lensa.com",
    # Added 2026-10-25 from the link audit / link report of the open queue:
    "secrethunter.io",      # 10 jobs; every page title is the site's own
    "secrettelaviv.com",    # jobs.secrettelaviv.com, 62 open jobs - a board
    "getajob.careers", "bebee.com",
    "yuvalhr.com",          # headhunter; posts client roles, often unnamed
)

# Built In runs one site per city (builtin.com, builtinaustin.com, builtinnyc.com...).
_BUILTIN_HOST = __import__("re").compile(r"^builtin[a-z]*\.com$")

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
    if _on(host, AGGREGATOR_DOMAINS) or _BUILTIN_HOST.match(host):
        return "aggregator"
    return "company"


def needs_resolving(url: str) -> bool:
    """Would a direct link be better than what this job carries now?"""
    return classify(url) in ("board", "aggregator", "redirect", "none")


def _read_rows(db, sql, args):
    """Rows from `db`: a connection (left open - the caller owns it) or a
    get_db callable (opened and closed here, before any slow network work, so
    a 45-second report never holds a pooled connection)."""
    # sqlite3.Connection is itself callable, so test for .execute instead.
    if not hasattr(db, "execute"):
        conn = db()
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()
    return [dict(r) for r in db.execute(sql, args).fetchall()]


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
        resolver = make_resolver(conn)

    sql = ("SELECT id, user_id, title, company, location, url, source, status FROM jobs "
           "WHERE status IN ('new','approved')")
    args = ()
    if user_id is not None:
        sql += " AND user_id=?"
        args = (user_id,)
    sql += " ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END, id DESC"
    rows = _read_rows(conn, sql, args)

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
    tried, resolved, unresolved, by_ats, incomplete = 0, [], [], {}, 0
    for r in pairs[:max(0, sample)]:
        if time.monotonic() - started > budget_s:
            break
        tried += 1
        import apply_engine as _ae
        _ae.begin_lookup()
        try:
            hit = resolver(r.get("company") or "", r.get("title") or "", r.get("location") or "", use_llm)
        except Exception as exc:  # one bad company must not sink the report
            hit = {"error": f"{type(exc).__name__}: {exc}"[:160]}
        if not (hit and hit.get("url")) and _ae.lookup_incomplete():
            incomplete += 1
        if hit and hit.get("url"):
            by_ats[hit.get("ats") or "?"] = by_ats.get(hit.get("ats") or "?", 0) + 1
            resolved.append({"job_id": r["id"], "company": r.get("company"), "title": r.get("title"),
                             "from": host_of(r.get("url")), "to": hit["url"], "ats": hit.get("ats"),
                             "matched_title": hit.get("matched_title"), "score": hit.get("score"),
                             "via": hit.get("via") or "name guess"})
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
            "learned_companies": len(getattr(resolver, "learned", {}) or {}),
            "resolved_via_learned": sum(1 for x in resolved if str(x.get("via", "")).startswith("learned")),
            "distinct_pairs_needing_link": len(pairs),
            "tried": tried,
            "not_reached": max(0, min(sample, len(pairs)) - tried),
            "resolved": len(resolved),
            "hit_rate": round(100.0 * len(resolved) / tried, 1) if tried else None,
            # Misses where a job board did not answer: not evidence of absence.
            "incomplete": incomplete,
            "by_ats": by_ats,
            "seconds": round(time.monotonic() - started, 1),
            "resolved_examples": resolved[:20],
            "unresolved": unresolved[:40],
        },
    }


# ── 1b: resolve open jobs to the company's own posting ──────────────────────

RETRY_AFTER_DAYS = 7          # an unresolved job is tried again after this
BATCH_LIMIT = 80              # rows per run; the rest are picked up next run
RUN_BUDGET_S = 240.0          # stop early so one run never holds the worker long


def _utc_stamp(dt=None) -> str:
    from datetime import datetime, timezone
    return (dt or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")


def resolve_open_jobs(get_db, user_id: int, resolver=None, use_llm: bool = True,
                      limit: int = BATCH_LIMIT, budget_s: float = RUN_BUDGET_S, now=None) -> dict:
    """Give the user's open jobs (New + Approved) a direct link where one exists.

    Touches only status new/approved rows of `user_id` - applied, passed and
    deferred history is never rewritten (Eran's scope decision, 2026-10-25).
    For each row not examined yet, or unresolved and last tried more than
    RETRY_AFTER_DAYS ago:
      * already direct / company site  -> recorded as such, url untouched
      * board, aggregator, redirect    -> resolver(company, title, location, use_llm);
        on a hit the url becomes the ATS posting, the old url moves to
        source_url, url_verified=1 (the ATS API listing it is the proof it is
        open). On a miss the type is recorded and link_attempts goes up.
    A hit already stored on another row of the same user is NOT written
    (UNIQUE(user_id, url)); the reason goes in link_note.

    Returns counts for the run detail line. Opens and closes its own
    connections, one short one per write, so a slow ATS never holds a
    connection from the pool.
    """
    import time
    from datetime import datetime, timedelta, timezone

    if resolver is None:
        resolver = make_resolver(get_db)

    now = now or datetime.now(timezone.utc)
    stamp = _utc_stamp(now)
    cutoff = _utc_stamp(now - timedelta(days=RETRY_AFTER_DAYS))

    conn = get_db()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, title, company, location, url, link_type, link_resolved_at FROM jobs "
            "WHERE user_id=? AND status IN ('new','approved') AND ("
            "  link_resolved_at IS NULL OR link_type='company' OR "
            "  (link_type IN ('board','aggregator','redirect','none') AND link_resolved_at < ?)"
            ") ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END, id DESC",
            (user_id, cutoff)).fetchall()]
    finally:
        conn.close()
    # A link recorded as 'company' is looked at again only if the host lists
    # have since reclassified it (2026-10-25: secrethunter.io, secrettelaviv.com,
    # builtin*.com... moved to aggregator). Still 'company' = nothing to do,
    # and no network call.
    rows = [r for r in rows if not (r.get("link_type") == "company" and classify(r.get("url")) == "company")]

    out = {"examined": 0, "already_direct": 0, "resolved": 0, "unresolved": 0, "incomplete": 0,
           "duplicate": 0, "left_for_next_run": 0}
    started = time.monotonic()
    for i, r in enumerate(rows[:max(0, limit)]):
        if time.monotonic() - started > budget_s:
            out["left_for_next_run"] = len(rows) - i
            break
        out["examined"] += 1
        t = classify(r.get("url"))
        if t in ("direct_ats", "company"):
            _write(get_db, "UPDATE jobs SET link_type=?, link_resolved_at=? WHERE id=? AND user_id=?",
                   (t, stamp, r["id"], user_id))
            out["already_direct"] += 1
            continue
        import apply_engine as _ae
        _ae.begin_lookup()
        try:
            hit = resolver(r.get("company") or "", r.get("title") or "", r.get("location") or "", use_llm)
        except Exception as exc:
            print("[links] resolve failed for job %s (%s): %s" % (r["id"], r.get("company"), exc))
            hit = None
            _ae._mark_lookup_failure()     # an exception is not an answer either
        new_url = (hit or {}).get("url") or ""
        if new_url:
            conn = get_db()
            try:
                clash = conn.execute("SELECT id FROM jobs WHERE user_id=? AND url=? AND id<>?",
                                     (user_id, new_url, r["id"])).fetchone()
            finally:
                conn.close()
            if clash:
                _write(get_db, "UPDATE jobs SET link_type=?, link_resolved_at=?, link_note=? "
                               "WHERE id=? AND user_id=?",
                       (t, stamp, "direct link already on job #%s" % clash[0], r["id"], user_id))
                out["duplicate"] += 1
                continue
            # Guarded on the url we read, so a row changed meanwhile is left alone.
            # Recorded as what it is: a Greenhouse hit can be the ATS page or
            # the company's own careers page (Wiz's absolute_url is
            # wiz.io/careers/job/<id>/:title?gh_jid=<id> - verified 2026-10-25
            # to serve the posting). Both are direct; neither is penalised.
            new_type = classify(new_url)
            if new_type not in ("direct_ats", "company"):
                new_type = "direct_ats"
            _write(get_db, "UPDATE jobs SET source_url=COALESCE(source_url, url), url=?, link_type=?, "
                           "link_resolved_at=?, url_verified=1, link_note=? "
                           "WHERE id=? AND user_id=? AND url=?",
                   (new_url, new_type, stamp, "%s match %s" % (hit.get("ats") or "ats", hit.get("score")),
                    r["id"], user_id, r.get("url")))
            out["resolved"] += 1
        elif _lookup_incomplete():
            # A board did not answer (timeout / 5xx / 429): this is not a
            # miss. Leave link_resolved_at as it was, so the job is tried
            # again on the next run and carries no "No direct link" penalty.
            _write(get_db, "UPDATE jobs SET link_note=? WHERE id=? AND user_id=?",
                   ("lookup incomplete (a job board did not answer) - retried next run", r["id"], user_id))
            out["incomplete"] += 1
        else:
            _write(get_db, "UPDATE jobs SET link_type=?, link_resolved_at=?, "
                           "link_attempts=COALESCE(link_attempts,0)+1 WHERE id=? AND user_id=?",
                   (t, stamp, r["id"], user_id))
            out["unresolved"] += 1
    if not out["left_for_next_run"]:
        out["left_for_next_run"] = max(0, len(rows) - max(0, limit))
    return out


def _write(get_db, sql, args):
    conn = get_db()
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def run_detail(counts: dict) -> str:
    c = {"incomplete": 0, **counts}
    return ("links: %(examined)d examined, %(resolved)d resolved, %(already_direct)d already direct, "
            "%(unresolved)d unresolved, %(incomplete)d incomplete, %(duplicate)d duplicate, "
            "%(left_for_next_run)d left for next run" % c)


def _lookup_incomplete() -> bool:
    import apply_engine as _ae
    return _ae.lookup_incomplete()


# ── 1d: ranking ─────────────────────────────────────────────────────────────

# Points a job drops in the ranking when we looked for the company's own
# posting and did not find it (Eran approved 5, 2026-10-25). Visible on the
# card as "No direct link" and in the score explanation, never hidden.
LINK_PENALTY = 5
_PENALISED = ("board", "aggregator", "redirect", "none")


def link_penalty(job) -> int:
    """5 for an open job whose resolve attempt found nothing; 0 otherwise -
    including jobs not examined yet, which have not had their chance."""
    get = job.get if hasattr(job, "get") else (lambda k: job[k])
    if not get("link_resolved_at"):
        return 0
    return LINK_PENALTY if (get("link_type") or "") in _PENALISED else 0


LINK_PENALTY_SQL = ("(CASE WHEN link_resolved_at IS NOT NULL AND link_type IN "
                    "('board','aggregator','redirect','none') THEN %d ELSE 0 END)" % LINK_PENALTY)


# ── Step 2: link audit (evidence before a rule) ─────────────────────────────

def _page_title(body: str) -> str:
    import html as _html
    import re
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", body or "")
    return re.sub(r"\s+", " ", _html.unescape(m.group(1))).strip()[:200] if m else ""


def _title_tokens(t: str) -> list:
    import apply_engine as _ae
    return [w for w in _ae._norm_title(t).split() if len(w) > 1]


def audit_one(job: dict, fetch=None, timeout: int = 15) -> dict:
    """What a job's link actually shows. Read-only: one GET, nothing stored.

    The point is to see real pages before writing a rule - the 10-12 attempt
    at a title-on-page check was rejected because it failed real Payoneer,
    NICE and LinkedIn postings, and the URLs it was judged on were not kept.
    """
    import re
    import urllib.parse as _up
    import linkcheck

    url = (job.get("url") or "").strip()
    out = {"job_id": job.get("id"), "company": job.get("company"), "title": job.get("title"),
           "url": url, "type": classify(url)}
    got = {}

    def _once(u, t):
        if "r" not in got:
            try:
                got["r"] = (fetch or linkcheck._fetch)(u, t)
            except Exception as exc:
                got["e"] = exc
        if "e" in got:
            raise got["e"]
        return got["r"]

    verdict, reason, final = linkcheck.classify(url, timeout=timeout, fetch=_once)
    out.update({"verdict": verdict, "reason": reason, "final_url": final})
    if "r" not in got:
        out["fetched"] = False
        return out
    status, final_url, body = got["r"]
    vis = linkcheck._visible_text(body)
    page_title = _page_title(body)
    toks = _title_tokens(job.get("title") or "")
    fp = _up.urlparse(final_url or url)
    out.update({
        "fetched": True,
        "status": status,
        "moved_host": host_of(final_url) != host_of(url) if final_url else False,
        "landed_on_root": not [s for s in (fp.path or "").split("/") if s],
        "landed_generic": linkcheck.is_generic_url(final_url or url),
        "page_title": page_title,
        "title_tokens": toks,
        "tokens_in_page_title": sum(1 for w in toks if w in page_title.lower()),
        "tokens_in_text": sum(1 for w in toks if re.search(r"\b%s\b" % re.escape(w), vis)),
        "company_in_page_title": bool(job.get("company")) and
                                 (job.get("company") or "").strip().lower()[:12] in page_title.lower(),
        "visible_chars": len(vis),
        "body_bytes": len(body or ""),
        # Little visible text in a big page = rendered by script; a title check
        # cannot judge these from the HTML (the Payoneer/NICE false negatives).
        "script_rendered": len(vis) < 1500 and len(body or "") > 20000,
    })
    return out


def link_audit(conn, user_id=None, types=("company", "aggregator"), limit: int = 40,
               budget_s: float = 50.0, workers: int = 8, fetch=None) -> dict:
    """Fetch the open jobs whose links are on company or aggregator sites and
    report what each page shows (audit_one). Read-only."""
    import time
    from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

    sql = ("SELECT id, user_id, title, company, location, url FROM jobs "
           "WHERE status IN ('new','approved')")
    args = ()
    if user_id is not None:
        sql += " AND user_id=?"
        args = (user_id,)
    sql += " ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END, id DESC"
    rows = _read_rows(conn, sql, args)
    picked = [r for r in rows if classify(r.get("url")) in types][:max(0, limit)]

    started = time.monotonic()
    results = []
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        futs = {pool.submit(audit_one, r, fetch): r for r in picked}
        pending = set(futs)
        while pending:
            left = budget_s - (time.monotonic() - started)
            if left <= 0:
                break
            done, pending = wait(pending, timeout=left, return_when=FIRST_COMPLETED)
            for f in done:
                try:
                    results.append(f.result())
                except Exception as exc:
                    r = futs[f]
                    results.append({"job_id": r["id"], "url": r.get("url"), "error": repr(exc)[:160]})
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    verdicts = {}
    for r in results:
        verdicts[r.get("verdict", "error")] = verdicts.get(r.get("verdict", "error"), 0) + 1
    return {
        "scope": "all users" if user_id is None else f"user {user_id}",
        "types": list(types),
        "candidates": len([r for r in rows if classify(r.get("url")) in types]),
        "audited": len(results),
        "not_reached": len(picked) - len(results),
        "seconds": round(time.monotonic() - started, 1),
        "verdicts": verdicts,
        "jobs": sorted(results, key=lambda r: (r.get("verdict") != "alive", r.get("job_id") or 0)),
    }


# ── 1c (option 1): learn each company's board from links we already hold ────
#
# Measured 2026-10-25 on the open queue: the name-guessing resolver found the
# company posting for 2 of 24 LinkedIn jobs (3 of 38 with Gemini slug guesses).
# But the database already holds hundreds of direct links (352 open jobs on
# Comeet, 210 on Greenhouse...), and each one names its company's board. A
# company seen once on a board can be looked up there for every other role.
#
# Comeet in particular: its public API is not addressable by company name (the
# resolver's Comeet branch found nothing for unframe, melio or lightricks), but
# a company page - comeet.com/jobs/<slug>/<company uid> - embeds every open
# position as COMPANY_POSITIONS_DATA, each with its Comeet page and its page on
# the company's own site (url_active_page). Verified live 2026-10-25 on
# Coralogix (64 positions), VAST Data (264), Global-e (49), Team8 (65).

import re as _re

_BOARD_RULES = (
    ("greenhouse", _re.compile(r"^https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([A-Za-z0-9_-]+)(?:/|$)", _re.I)),
    ("lever", _re.compile(r"^https?://jobs\.lever\.co/([A-Za-z0-9_.-]+)(?:/|$)", _re.I)),
    ("ashby", _re.compile(r"^https?://jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)(?:/|$)", _re.I)),
    ("smartrecruiters", _re.compile(r"^https?://jobs\.smartrecruiters\.com/([A-Za-z0-9_-]+)(?:/|$)", _re.I)),
    ("comeet", _re.compile(r"^https?://(?:www\.)?comeet\.(?:com|co)/jobs/([A-Za-z0-9_-]+)/([0-9A-F]{2}\.[0-9A-F]{3})(?:/|$|\?)", _re.I)),
)
_NOT_A_BOARD = {"embed", "jobs", "careers", "api", "v0", "v1"}


def board_of(url: str):
    """('greenhouse', 'wizinc') / ('comeet', 'coralogix/06.004') / None."""
    for ats, rx in _BOARD_RULES:
        m = rx.match((url or "").strip())
        if not m:
            continue
        if ats == "comeet":
            return ats, "%s/%s" % (m.group(1).lower(), m.group(2).upper())
        slug = m.group(1).lower()
        return (ats, slug) if slug not in _NOT_A_BOARD else None
    return None


def _norm_company(name: str) -> str:
    n = (name or "").lower()
    n = _re.sub(r"\((.*?)\)", " ", n)                       # "Yuval HR (Gaming Startup)"
    n = _re.sub(r"\b(inc|ltd|llc|corp|gmbh|co|the|technologies|technology|group)\b\.?", " ", n)
    n = _re.sub(r"\.(com|io|ai|co)\b", " ", n)
    return _re.sub(r"[^a-z0-9]+", "", n)


_BOARD_LIKES = ("%greenhouse.io/%", "%jobs.lever.co/%", "%jobs.ashbyhq.com/%",
                "%jobs.smartrecruiters.com/%", "%comeet.com/jobs/%", "%comeet.co/jobs/%")


def learned_boards(db) -> dict:
    """{normalised company: [(ats, ref), ...]} from every stored direct link,
    any user, any status - a company's job board is public, not a user's data."""
    sql = ("SELECT DISTINCT company, url FROM jobs WHERE "
           + " OR ".join(["url LIKE ?"] * len(_BOARD_LIKES)))
    out: dict = {}
    for r in _read_rows(db, sql, _BOARD_LIKES):
        b = board_of(r.get("url"))
        key = _norm_company(r.get("company"))
        if b and key and b not in out.setdefault(key, []):
            out[key].append(b)
    return out


_COMEET_CACHE: dict = {}
_COMEET_MAX_BYTES = 6_000_000   # Coralogix's page is ~508 KB; VAST Data lists 264 positions


def _fetch_full(url: str, timeout: int):
    """linkcheck._fetch reads 400 KB, enough to judge a page but not to hold a
    big company's whole positions list (Coralogix's ran past it, 2026-10-25)."""
    import urllib.request
    import linkcheck
    req = urllib.request.Request(url, headers={"User-Agent": linkcheck.UA, "Accept-Language": "en-US,en;q=0.9"})
    with linkcheck._OPENER.open(req, timeout=timeout) as r:
        return r.status, r.geturl(), r.read(_COMEET_MAX_BYTES).decode("utf-8", "ignore")


def comeet_postings(ref: str, fetch=None) -> list:
    """[{title, url, location, ats}] for comeet ref 'slug/UID', from the positions
    the company page embeds. url = the posting on the company's own site when
    Comeet knows it, else the Comeet page. Cached for 10 minutes."""
    import json
    import time
    import linkcheck
    hit = _COMEET_CACHE.get(ref)
    if hit and time.monotonic() - hit[0] < 600:
        return list(hit[1])
    out = []
    try:
        _st, _final, body = (fetch or _fetch_full)("https://www.comeet.com/jobs/%s" % ref, 20)
        i = body.find("COMPANY_POSITIONS_DATA =")
        if i >= 0:
            data, _ = json.JSONDecoder().raw_decode(body[i + len("COMPANY_POSITIONS_DATA ="):].lstrip())
            for p in data if isinstance(data, list) else []:
                loc = p.get("location")
                loc = loc.get("name", "") if isinstance(loc, dict) else str(loc or "")
                url = p.get("url_active_page") or p.get("url_comeet_hosted_page") or ""
                if p.get("name") and url:
                    out.append({"title": p["name"], "url": url, "location": loc, "ats": "comeet"})
    except Exception as exc:
        print("[links] comeet %s: %s" % (ref, exc))
        import apply_engine as _ae
        _ae._mark_lookup_failure()          # no answer - not "no positions"
        return []
    if len(_COMEET_CACHE) > 300:
        _COMEET_CACHE.clear()
    _COMEET_CACHE[ref] = (time.monotonic(), out)
    return list(out)


def resolve_from_learned(company, title, location, learned, fetch=None, threshold: float = 82.0):
    """Best posting for this title on the boards this company is known to use."""
    import apply_engine as _ae
    boards = learned.get(_norm_company(company)) or []
    best, best_key = None, (-1, -1.0)
    for ats, ref in boards:
        posts = comeet_postings(ref, fetch=fetch) if ats == "comeet" else _ae._ats_postings_for_slug(ref)
        for p in posts:
            score = _ae._title_ratio(title, p.get("title") or "")
            if score < threshold:
                continue
            key = (_ae._loc_match(p.get("location") or "", location or ""), score)
            if key > best_key:
                best_key = key
                best = {"url": p["url"], "ats": p.get("ats") or ats, "matched_title": p.get("title"),
                        "score": round(score, 1), "via": "learned board %s:%s" % (ats, ref)}
    return best


def make_resolver(db, fetch=None):
    """The resolver chain Step 1 uses: learned boards first (free, exact board),
    then apply_engine's name-guessing resolver."""
    import apply_engine as _ae
    learned = learned_boards(db)

    def resolver(company, title, location, use_llm):
        _ae.begin_lookup()
        hit = resolve_from_learned(company, title, location, learned, fetch=fetch)
        if hit:
            return hit
        return _ae.resolve_ats_application(company, title, location=location, use_llm=use_llm)
    resolver.learned = learned
    return resolver
