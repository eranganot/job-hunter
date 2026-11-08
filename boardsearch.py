"""Step 16: board-first search (Eran, 2026-11-08).

The share of open jobs with a direct link follows the share of *incoming* jobs
that arrive direct. Fixing LinkedIn links afterwards resolves ~14-16% of them
(measured), so the search itself must bring more jobs straight from company job
boards. This module searches every board the app already knows:

  - boards behind direct links stored for any user (Comeet, Greenhouse, Lever,
    Ashby, SmartRecruiters), via links.board_of;
  - boards the careers-map found on company careers pages (company_boards).

Workday is left to workday.py, which already searches its verified tenants.

Unlike the fixed board lists in app.py, whose every posting goes to the AI
scorer, postings here are filtered BEFORE scoring - the user's titles (the
caller's matcher) and an Israel location - so hundreds of boards cost requests,
not AI calls. Each board is asked once per BOARD_TTL_S across all users.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

import links

BOARD_TTL_S = 3 * 3600
MAX_BOARDS = 400
_CACHE: dict = {}                    # (ats, ref) -> (monotonic, [postings])
_ATS = ("comeet", "greenhouse", "lever", "ashby", "smartrecruiters")


def known_boards(get_db) -> list:
    """[(ats, ref, company)] - one per board, most-used company name."""
    from collections import Counter
    names: dict = {}
    rows = links._read_rows(get_db, "SELECT company, url FROM jobs WHERE "
                            + " OR ".join(["url LIKE ?"] * len(links._BOARD_LIKES)), links._BOARD_LIKES)
    for r in rows:
        b = links.board_of(r.get("url"))
        if b and b[0] in _ATS and (r.get("company") or "").strip():
            names.setdefault(b, Counter())[r["company"].strip()] += 1
    try:
        for r in links._read_rows(get_db, "SELECT company_key, boards FROM company_boards", ()):
            for ats, ref in json.loads(r.get("boards") or "[]"):
                if ats in _ATS:
                    names.setdefault((ats, ref), Counter())[r["company_key"]] += 0
    except Exception as exc:                 # a database without m0024
        print("[boards] WARNING company_boards unreadable: %s" % exc)
    out = []
    for (ats, ref), c in names.items():
        company = c.most_common(1)[0][0] if c else ref.split("/")[0]
        out.append((ats, ref, company))
    return sorted(out)[:MAX_BOARDS]


def _get(url):
    import apply_engine as ae
    return ae._ats_get(url)


def postings(ats: str, ref: str, get=None) -> list:
    """[{title, url, location, description}] for one board. Cached BOARD_TTL_S;
    a board that did not answer is not cached as empty."""
    key = (ats, ref)
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < BOARD_TTL_S:
        return list(hit[1])
    get = get or _get
    out, answered = [], True
    try:
        if ats == "comeet":
            out = [{"title": p["title"], "url": p["url"], "location": p.get("location") or "", "description": ""}
                   for p in links.comeet_postings(ref)]
            answered = bool(out)          # comeet_postings returns [] on failure too: never cache that
        elif ats == "greenhouse":
            d = get("https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true" % ref)
            answered = isinstance(d, dict)
            for j in (d or {}).get("jobs", []) if answered else []:
                loc = j.get("location") or {}
                text = re.sub(r"<[^>]+>", " ", re.sub(r"&lt;|&gt;", " ", j.get("content") or ""))
                out.append({"title": j.get("title", ""), "location": loc.get("name", "") if isinstance(loc, dict) else "",
                            "url": j.get("absolute_url") or "https://boards.greenhouse.io/%s/jobs/%s" % (ref, j.get("id", "")),
                            "description": re.sub(r"\s+", " ", text).strip()[:5000]})
        elif ats == "lever":
            d = get("https://api.lever.co/v0/postings/%s?mode=json" % ref)
            answered = isinstance(d, list)
            for j in d if answered else []:
                cats = j.get("categories") or {}
                loc = cats.get("location") or ((cats.get("allLocations") or [""])[0])
                out.append({"title": j.get("text", ""), "location": loc or "",
                            "url": j.get("hostedUrl") or "https://jobs.lever.co/%s/%s" % (ref, j.get("id", "")),
                            "description": (j.get("descriptionPlain") or "")[:5000]})
        elif ats == "ashby":
            d = get("https://api.ashbyhq.com/posting-api/job-board/%s?includeCompensation=false" % ref)
            answered = isinstance(d, dict)
            for j in (d or {}).get("jobs", []) if answered else []:
                if j.get("isListed") is False:
                    continue
                out.append({"title": j.get("title", ""), "location": j.get("location") or "",
                            "url": j.get("jobUrl") or "https://jobs.ashbyhq.com/%s/%s" % (ref, j.get("id", "")),
                            "description": (j.get("descriptionPlain") or "")[:5000]})
        elif ats == "smartrecruiters":
            d = get("https://api.smartrecruiters.com/v1/companies/%s/postings?limit=100" % ref)
            answered = isinstance(d, dict)
            for j in (d or {}).get("content", []) if answered else []:
                lo = j.get("location") or {}
                loc = ", ".join(x for x in (lo.get("city"), lo.get("country")) if x)
                out.append({"title": j.get("name", ""), "location": loc,
                            "url": "https://jobs.smartrecruiters.com/%s/%s" % (ref, j.get("id", "")),
                            "description": ""})
    except Exception as exc:
        print("[boards] %s %s: %s" % (ats, ref, exc))
        return []
    out = [p for p in out if p.get("title") and p.get("url")]
    if answered:
        if len(_CACHE) > 2000:
            _CACHE.clear()
        _CACHE[key] = (time.monotonic(), out)
    return list(out)


def _israel(location: str) -> bool:
    """Israel, or no location at all (a board can omit it; the scorer and the
    relevance gate still judge the job). "Remote" alone is not Israel."""
    import workday
    loc = (location or "").strip()
    return not loc or workday.is_israel(loc)


def search(get_db, title_match, budget_s: float = 75.0, workers: int = 12, boards=None, get=None) -> tuple:
    """(jobs, stats). jobs: raw search results, already direct, title- and
    Israel-filtered. Stops at budget_s - the boards not reached are counted."""
    boards = known_boards(get_db) if boards is None else boards
    stats = {"boards": len(boards), "asked": 0, "postings": 0, "kept": 0, "not_reached": 0, "by_ats": {}}
    jobs, seen = [], set()
    t0 = time.monotonic()
    ex = ThreadPoolExecutor(max_workers=workers)
    try:
        futs = {ex.submit(postings, ats, ref, get): (ats, ref, company) for ats, ref, company in boards}
        pending = set(futs)
        while pending and time.monotonic() - t0 < budget_s:
            done, pending = wait(pending, timeout=max(0.1, budget_s - (time.monotonic() - t0)),
                                 return_when=FIRST_COMPLETED)
            for f in done:
                ats, ref, company = futs[f]
                stats["asked"] += 1
                try:
                    ps = f.result()
                except Exception as exc:
                    print("[boards] %s %s failed: %s" % (ats, ref, exc))
                    continue
                stats["postings"] += len(ps)
                for p in ps:
                    if p["url"] in seen or not _israel(p.get("location")) or not title_match(p["title"]):
                        continue
                    seen.add(p["url"])
                    jobs.append({"job_title": p["title"], "title": p["title"], "company": company,
                                 "location": p.get("location") or "Israel", "url": p["url"],
                                 "description": (p.get("description") or p["title"])[:300],
                                 "full_description": p.get("description") or p["title"],
                                 "source": "board-" + ats})
                    stats["by_ats"][ats] = stats["by_ats"].get(ats, 0) + 1
        stats["not_reached"] = len(pending)
        for f in pending:
            f.cancel()
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    stats["kept"] = len(jobs)
    stats["seconds"] = round(time.monotonic() - t0, 1)
    return jobs, stats
