"""
Managed-scraping adapters — PAID tier (admin only).

  Apify   — runs hosted "Actors" that scrape localized Israeli boards
            (AllJobs / Drushim / etc.). Configure actor ids via APIFY_ACTORS.
  JobSpy  — the `python-jobspy` library aggregating LinkedIn / Indeed /
            Glassdoor / ZipRecruiter. Lazy-imported so its (heavy) deps are
            optional; routes through the residential proxy pool to avoid bans.

Both degrade cleanly: no token / library missing → SKIPPED, never a crash.
"""
from __future__ import annotations

from .base import SourceAdapter, AdapterStatus, AdapterResult
from ..models import JobSource, SourceTier, SourceCategory, ApplyType, _coerce_date
from ..config import config


def _phrase(query) -> str:
    parts = (query.titles or []) + (query.keywords or [])
    return parts[0] if parts else "product manager"


# ─────────────────────────── Apify ─────────────────────────────────────────

class ApifyAdapter(SourceAdapter):
    """Run Apify Actors for localized Israeli job boards."""
    name = JobSource.APIFY
    tier = SourceTier.PAID
    category = SourceCategory.MANAGED_SCRAPE
    required_env = ("APIFY_TOKEN", "APIFY_ACTORS")
    credit_key = "apify"

    def _fetch(self, query):
        out = []
        actors = [a.strip() for a in (config.APIFY_ACTORS or "").split(",") if a.strip()]
        for actor in actors:
            actor_path = actor.replace("/", "~")     # Apify wants user~actor
            url = (f"https://api.apify.com/v2/acts/{actor_path}/"
                   f"run-sync-get-dataset-items?token={config.APIFY_TOKEN}")
            payload = {
                "queries": query.titles or [_phrase(query)],
                "location": query.locations[0] if query.locations else "Israel",
                "maxItems": query.limit_per_source,
            }
            items = self.http.post_json(url, json_body=payload)
            if not isinstance(items, list):
                continue
            for it in items[: query.limit_per_source]:
                out.append(self._mk(
                    title=it.get("title") or it.get("positionName") or it.get("job_title") or "",
                    company=it.get("company") or it.get("companyName") or "",
                    location=it.get("location") or "",
                    url=it.get("url") or it.get("jobUrl") or it.get("link") or "",
                    description=(it.get("description") or "")[:600],
                    full_description=it.get("description") or "",
                    posted_at=_coerce_date(it.get("postedAt") or it.get("date")),
                    apply_type=ApplyType.UNKNOWN,
                    source_job_id=str(it.get("id") or it.get("jobId") or ""),
                    extra={"apify_actor": actor},
                ))
        return out


# ─────────────────────────── JobSpy ────────────────────────────────────────

class JobSpyAdapter(SourceAdapter):
    """
    LinkedIn / Indeed / Glassdoor aggregation via the python-jobspy library.

    The library is an optional dependency (it pulls pandas) — if it's not
    installed we skip cleanly. When proxies are configured we pass them straight
    through to jobspy so heavy scraping doesn't get the host IP banned.
    """
    name = JobSource.JOBSPY
    tier = SourceTier.PAID
    category = SourceCategory.MANAGED_SCRAPE
    required_env = ()                 # no API key; gated by tier + library presence
    credit_key = None

    def fetch(self, query) -> AdapterResult:
        if not config.JOBSPY_ENABLED:
            return AdapterResult(self.name, AdapterStatus.SKIPPED_TIER,
                                 message="JOBSPY_ENABLED is off")
        try:
            from jobspy import scrape_jobs  # type: ignore
        except Exception:
            return AdapterResult(
                self.name, AdapterStatus.SKIPPED_NO_CREDS,
                message="python-jobspy not installed (pip install python-jobspy)")
        try:
            jobs = self._run(scrape_jobs, query)
            return AdapterResult(self.name, AdapterStatus.OK, jobs=jobs)
        except Exception as e:
            return AdapterResult(self.name, AdapterStatus.ERROR,
                                 message=f"{type(e).__name__}: {e}")

    def _run(self, scrape_jobs, query) -> list:
        sites = [s.strip() for s in (config.JOBSPY_SITES or "").split(",") if s.strip()]
        proxies = None
        if self.proxies and self.proxies.active:
            # jobspy accepts a list of proxy URLs and rotates them itself
            proxies = [p for p in (self.proxies.get() for _ in range(5)) if p]
        df = scrape_jobs(
            site_name=sites or ["linkedin", "indeed", "glassdoor"],
            search_term=_phrase(query),
            location=query.locations[0] if query.locations else "Israel",
            results_wanted=query.limit_per_source,
            hours_old=24 * 30,
            country_indeed="israel",
            proxies=proxies,
            linkedin_fetch_description=True,
        )
        out = []
        try:
            records = df.to_dict("records")     # pandas DataFrame → list[dict]
        except Exception:
            records = df if isinstance(df, list) else []
        for r in records[: query.limit_per_source]:
            site = (r.get("site") or "").lower()
            out.append(self._mk(
                title=r.get("title", ""),
                company=r.get("company") or "",
                location=str(r.get("location") or ""),
                url=r.get("job_url") or r.get("job_url_direct") or "",
                description=(str(r.get("description") or ""))[:600],
                full_description=str(r.get("description") or ""),
                posted_at=_coerce_date(r.get("date_posted")),
                apply_type=ApplyType.MANUAL_REQUIRED,   # board listings = manual apply
                source_job_id=str(r.get("id") or ""),
                remote=r.get("is_remote"),
                extra={"jobspy_site": site},
            ))
        return out
