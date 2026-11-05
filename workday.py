"""Workday career sites: one place for the tenants we know, their public search
API, and the two things the app does with them.

1. A job source (the search loop in app.py): each tenant is searched for the
   user's titles and only Israeli postings are kept.
2. A link resolver (links.py, Step 1c): a LinkedIn job at a company on Workday
   is matched to that company's own posting.

Why this module exists (2026-11-05): the search loop held a hard-coded list of
ten tenants and wrapped each call in ``except Exception: pass``. Probed live
with the app's exact request, eight of the ten were dead - NVIDIA and PayPal
answered 404 "not found: Job_Posting_Site_ID", IBM, eBay, HPE, VMware,
Accenture and Dell answered 422 and their career-site pages 500 - so for as
long as those ids were stale the loop silently searched Intel and Salesforce
only, and it asked for the first 20 postings worldwide (searchText ""), about
one of which was in Israel. Every failure is now counted and reported in
/api/health under ``sources.workday``.

Workday's public API, per tenant ``https://<tenant>.<wdN>.myworkdayjobs.com``:
  POST /wday/cxs/<tenant>/<site>/jobs
       {"appliedFacets": {}, "limit": <=20, "offset": 0, "searchText": "..."}
  -> {"total": N, "jobPostings": [{"title", "locationsText", "externalPath"}]}
``externalPath`` starts with the posting's primary location
("/job/Israel-Yokneam/Senior-Product-Manager_JR2019895"), which is how an
Israeli posting is recognised when ``locationsText`` only says "2 Locations".
The public posting is ``https://<tenant>.<wdN>.myworkdayjobs.com/<site><externalPath>``
(the same URL the detail endpoint returns as ``externalUrl``).
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) JobHunter/1.0"

# Every row verified live on 2026-11-05: the search API answered 200 and
# "Israel" returned Israeli postings. Tenants that answered but had no Israeli
# postings (Broadcom/VMware, Adobe) are left out; eBay closed its Israel site in
# May 2025; IBM, Dell, Check Point, CyberArk, Palo Alto, Amdocs, NICE and
# Mobileye did not answer on the ids tried (422/401) - most run their own sites.
# (company, tenant, wdN, site, aliases the company appears under on LinkedIn)
TENANTS = (
    ("NVIDIA", "nvidia", "wd5", "NVIDIAExternalCareerSite", ("Mellanox", "Run:ai")),
    ("Intel", "intel", "wd1", "External", ("Intel Corporation",)),
    ("Salesforce", "salesforce", "wd12", "External_Career_Site", ()),
    ("PayPal", "paypal", "wd1", "jobs", ()),
    ("Accenture", "accenture", "wd103", "AccentureCareers", ()),
    ("HPE", "hpe", "wd5", "ACJobSite", ("Hewlett Packard Enterprise",)),
    ("Applied Materials", "amat", "wd1", "External", ()),
    ("Marvell", "marvell", "wd1", "MarvellCareers", ("Marvell Technology",)),
    ("Motorola Solutions", "motorolasolutions", "wd5", "Careers", ()),
    ("Philips", "philips", "wd3", "jobs-and-careers", ()),
    ("Mastercard", "mastercard", "wd1", "CorporateCareers", ()),
    ("Cadence", "cadence", "wd1", "External_Careers", ("Cadence Design Systems",)),
    ("Citi", "citi", "wd5", "2", ("Citigroup", "Citibank")),
    ("Red Hat", "redhat", "wd5", "jobs", ()),
    ("Autodesk", "autodesk", "wd1", "Ext", ()),
    ("Workday", "workday", "wd5", "Workday", ()),
)

IL_TOKENS = ("israel", "tel aviv", "tel-aviv", "herzliya", "haifa", "yokneam",
             "jerusalem", "petah tikva", "petah-tikva", "raanana", "ra'anana",
             "netanya", "kiryat", "caesarea", "rehovot", "hod hasharon",
             "ramat gan", "airport city", "or yehuda", "beer sheva", "matam")

_URL_RX = re.compile(
    r"^https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)/job/",
    re.I)


def ref_of(url: str):
    """'nvidia.wd5/NVIDIAExternalCareerSite' for a Workday posting URL, else None."""
    m = _URL_RX.match((url or "").strip())
    if not m:
        return None
    return "%s.%s/%s" % (m.group(1).lower(), m.group(2).lower(), m.group(3))


def _split(ref: str):
    host, site = ref.split("/", 1)
    tenant, wd = host.split(".", 1)
    return tenant, wd, site


def tenant_refs() -> list:
    """[(company, ref, aliases)] for the verified tenants."""
    return [(c, "%s.%s/%s" % (t, wd, s), a) for c, t, wd, s, a in TENANTS]


def _location(p: dict) -> str:
    """Primary location from the path plus the summary text, e.g.
    'Israel-Yokneam | 2 Locations'."""
    m = re.match(r"^/job/([^/]+)/", p.get("externalPath") or "")
    return " | ".join(x for x in ((m.group(1) if m else ""), p.get("locationsText") or "") if x)


def is_israel(location: str) -> bool:
    loc = (location or "").lower().replace("-", " ")
    return any(t.replace("-", " ") in loc for t in IL_TOKENS)


class WorkdayError(Exception):
    pass


def _post(ref: str, text: str, limit: int, timeout: int):
    """Search one tenant. Raises WorkdayError on any refusal or no answer, so a
    stale id is never mistaken for 'no jobs today'."""
    tenant, wd, site = _split(ref)
    url = "https://%s.%s.myworkdayjobs.com/wday/cxs/%s/%s/jobs" % (tenant, wd, tenant, site)
    body = json.dumps({"appliedFacets": {}, "limit": limit, "offset": 0,
                       "searchText": text}).encode("utf-8")
    last = ""
    gate = _cluster_gate(wd)
    for attempt, t in enumerate((timeout, max(timeout, 15), max(timeout, 15))):
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json", "Accept": "application/json", "User-Agent": UA})
        try:
            with gate:
                with urllib.request.urlopen(req, timeout=t) as r:
                    return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = "HTTP %s" % e.code
            if e.code == 429:
                # Observed 2026-11-05: ~21 calls to the wd1 cluster inside 10 s
                # drew an occasional 429 (1 of 48 calls; the same burst again: 0),
                # while 9 parallel calls alone never did - short-window throttling.
                time.sleep(_retry_after(e, attempt))
                continue
            if 400 <= e.code < 500 and e.code != 408:
                break                     # definitive: this tenant/site id is wrong
        except ValueError:
            last = "not JSON"
            break
        except Exception as e:            # timeout / network: retried
            last = type(e).__name__
    raise WorkdayError("%s from %s" % (last, ref))


_GATES: dict = {}
_GATES_LOCK = threading.Lock()
PER_CLUSTER = 3


def _cluster_gate(wd: str):
    """At most PER_CLUSTER calls in flight per Workday cluster (wd1, wd5...)."""
    with _GATES_LOCK:
        g = _GATES.get(wd)
        if g is None:
            g = _GATES[wd] = threading.BoundedSemaphore(PER_CLUSTER)
        return g


def _retry_after(err, attempt: int) -> float:
    try:
        v = float(err.headers.get("Retry-After") or 0)
    except Exception:
        v = 0.0
    return min(v, 10.0) if v > 0 else 2.0 * (attempt + 1)


_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_S = 600


def search(ref: str, text: str, limit: int = 20, timeout: int = 10) -> list:
    """[{title, url, location, ats}] for one tenant and search text. Cached for
    10 minutes (a search run can carry several jobs at one company). Raises
    WorkdayError when the tenant does not answer; nothing is cached then."""
    key = (ref, (text or "").strip().lower(), limit)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL_S:
        return list(hit[1])
    tenant, wd, site = _split(ref)
    data = _post(ref, text, limit, timeout)
    out = []
    for p in data.get("jobPostings") or []:
        path = p.get("externalPath") or ""
        title = (p.get("title") or "").strip()
        if not (path and title):
            continue
        out.append({"title": title,
                    "url": "https://%s.%s.myworkdayjobs.com/%s%s" % (tenant, wd, site, path),
                    "location": _location(p), "ats": "workday"})
    with _CACHE_LOCK:
        if len(_CACHE) > 500:
            _CACHE.clear()
        _CACHE[key] = (time.monotonic(), out)
    return list(out)


def postings_for(ref: str, title: str) -> list:
    """Resolver use: this tenant's postings for one job title, Israel-weighted.
    A tenant that does not answer is counted as an incomplete lookup (so the job
    is retried, not penalised) and gives []."""
    import apply_engine as _ae
    try:
        return search(ref, "%s Israel" % (title or ""), limit=20)
    except WorkdayError as e:
        print("[workday] resolver: %s" % e)
        _ae._mark_lookup_failure()
        return []


def search_israel(titles, title_match=None, max_titles: int = 3, workers: int = 8):
    """Job-source use: every verified tenant x the first `max_titles` titles.
    Returns (jobs, stats). jobs are Israeli postings that pass `title_match`,
    one per URL; stats = {fetched, kept, failed: {ref: reason}, tenants}."""
    titles = [t for t in (titles or []) if (t or "").strip()][:max_titles] or [""]
    tasks = [(c, ref, t) for c, ref, _a in tenant_refs() for t in titles]
    failed: dict = {}
    fetched = 0
    seen, jobs = set(), []

    def one(task):
        company, ref, title = task
        try:
            return company, ref, search(ref, ("%s Israel" % title).strip()), None
        except WorkdayError as e:
            return company, ref, [], str(e)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for company, ref, posts, err in ex.map(one, tasks):
            if err:
                failed[ref] = err
                continue
            fetched += len(posts)
            for p in posts:
                if p["url"] in seen or not is_israel(p["location"]):
                    continue
                if title_match is not None and not title_match(p["title"]):
                    continue
                seen.add(p["url"])
                jobs.append(dict(p, company=company))
    stats = {"fetched": fetched, "kept": len(jobs), "failed": failed,
             "tenants": len(TENANTS)}
    return jobs, stats


def health_line(stats: dict):
    """(status, message) for /api/health's sources.workday. Any tenant failing is
    an error - the old loop hid eight dead tenants as 'Workday: N jobs matched'."""
    failed = stats.get("failed") or {}
    if not failed:
        return "ok", ""
    status = "error" if len(failed) == stats.get("tenants") else "degraded"
    shown = "; ".join("%s" % v for v in list(failed.values())[:4])
    return status, "%d of %d tenants failed: %s" % (len(failed), stats.get("tenants"), shown)
