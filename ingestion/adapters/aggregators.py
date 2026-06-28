"""
Commercial aggregator adapters — PAID tier (admin only).

These require API keys. Until keys are set in Railway env, each one reports
SKIPPED_NO_CREDS and the pipeline proceeds without it (the "stub now, wire via
env vars" posture). The request/normalize code is real, so the day a key lands
the source just works.

Each also routes through the CreditManager so monthly caps are enforced and a
flaky provider trips the circuit breaker instead of failing every run.

  TheirStack  POST api.theirstack.com/v1/jobs/search   (Bearer; technographic filters)
  Adzuna      GET  api.adzuna.com/v1/api/jobs/{country}/search/{page}  (app_id+app_key)
  Coresignal  POST api.coresignal.com/cdapi/v2/job_base/search/filter  (apikey header)
"""
from __future__ import annotations

from .base import SourceAdapter
from ..models import JobSource, SourceTier, SourceCategory, ApplyType, _coerce_date
from ..config import config


def _phrase(query) -> str:
    parts = (query.titles or []) + (query.keywords or [])
    return parts[0] if parts else "product manager"


# ─────────────────────────── TheirStack ────────────────────────────────────

class TheirStackAdapter(SourceAdapter):
    """Technographic job search — filter by the tech stack a company uses."""
    name = JobSource.THEIRSTACK
    tier = SourceTier.PAID
    category = SourceCategory.AGGREGATOR
    required_env = ("THEIRSTACK_API_KEY",)
    credit_key = "theirstack"
    BASE = "https://api.theirstack.com/v1/jobs/search"

    def _fetch(self, query):
        out = []
        body = {
            "page": 0,
            "limit": query.limit_per_source,
            "job_title_or": query.titles or [_phrase(query)],
            "job_country_code_or": ["IL"],
            "posted_at_max_age_days": 30,
            "include_total_results": False,
        }
        # Optional technographic filter (e.g. companies using specific stacks)
        if query.keywords:
            body["company_technology_slug_or"] = query.keywords[:5]
        data = self.http.post_json(self.BASE, json_body=body, headers={
            "Authorization": f"Bearer {config.THEIRSTACK_API_KEY}",
            "Content-Type": "application/json",
        })
        if not data:
            return out
        for j in (data.get("data") or [])[: query.limit_per_source]:
            comp = j.get("company_object") or {}
            out.append(self._mk(
                title=j.get("job_title", ""),
                company=j.get("company") or comp.get("name", ""),
                location=j.get("location") or j.get("short_location") or "",
                url=j.get("url") or j.get("final_url") or j.get("source_url") or "",
                description=(j.get("description") or "")[:600],
                full_description=j.get("description") or "",
                posted_at=_coerce_date(j.get("date_posted")),
                apply_type=ApplyType.UNKNOWN,
                source_job_id=str(j.get("id") or ""),
                remote=j.get("remote"),
            ))
        return out


# ─────────────────────────── Adzuna ────────────────────────────────────────

class AdzunaAdapter(SourceAdapter):
    """Global aggregation. NOTE: Adzuna has no Israel feed — use for global/remote."""
    name = JobSource.ADZUNA
    tier = SourceTier.PAID
    category = SourceCategory.AGGREGATOR
    required_env = ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")
    credit_key = "adzuna"

    def _fetch(self, query):
        out = []
        country = config.ADZUNA_COUNTRY or "gb"
        url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
        params = {
            "app_id": config.ADZUNA_APP_ID,
            "app_key": config.ADZUNA_APP_KEY,
            "what": _phrase(query),
            "results_per_page": min(query.limit_per_source, 50),
            "max_days_old": 30,
            "content-type": "application/json",
        }
        if query.remote_ok:
            params["what_or"] = "remote"
        data = self.http.get_json(url, params=params)
        if not data:
            return out
        for j in (data.get("results") or [])[: query.limit_per_source]:
            comp = (j.get("company") or {}).get("display_name", "")
            loc = (j.get("location") or {}).get("display_name", "")
            out.append(self._mk(
                title=j.get("title", ""),
                company=comp,
                location=loc,
                url=j.get("redirect_url") or "",
                description=(j.get("description") or "")[:600],
                full_description=j.get("description") or "",
                posted_at=_coerce_date(j.get("created")),
                apply_type=ApplyType.UNKNOWN,
                source_job_id=str(j.get("id") or ""),
            ))
        return out


# ─────────────────────────── Coresignal ────────────────────────────────────

class CoresignalAdapter(SourceAdapter):
    """Normalized firmographic/jobs datasets. Two-step: filter → collect by id."""
    name = JobSource.CORESIGNAL
    tier = SourceTier.PAID
    category = SourceCategory.AGGREGATOR
    required_env = ("CORESIGNAL_API_KEY",)
    credit_key = "coresignal"
    SEARCH = "https://api.coresignal.com/cdapi/v2/job_base/search/filter"
    COLLECT = "https://api.coresignal.com/cdapi/v2/job_base/collect/"

    def _fetch(self, query):
        out = []
        headers = {
            "apikey": config.CORESIGNAL_API_KEY,
            "Content-Type": "application/json",
        }
        body = {
            "title": _phrase(query),
            "location": query.locations[0] if query.locations else "Israel",
            "application_active": True,
        }
        ids = self.http.post_json(self.SEARCH, json_body=body, headers=headers)
        if not isinstance(ids, list):
            return out
        for jid in ids[: min(query.limit_per_source, 25)]:    # collect costs credits
            rec = self.http.get_json(f"{self.COLLECT}{jid}", headers=headers)
            if not isinstance(rec, dict):
                continue
            out.append(self._mk(
                title=rec.get("title", ""),
                company=rec.get("company_name", ""),
                location=rec.get("location", ""),
                url=rec.get("url") or rec.get("external_url") or "",
                description=(rec.get("description") or "")[:600],
                full_description=rec.get("description") or "",
                posted_at=_coerce_date(rec.get("created") or rec.get("time_posted")),
                apply_type=ApplyType.UNKNOWN,
                source_job_id=str(jid),
            ))
        return out
