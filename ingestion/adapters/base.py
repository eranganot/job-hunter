"""
SourceAdapter — the common contract every source implements.

The pipeline never knows whether it's talking to a Big-Tech JSON endpoint or a
commercial aggregator: it just calls ``adapter.fetch(query)`` and gets back an
``AdapterResult`` (status + list[NormalizedJob]). This is what makes adding a new
source a ~40-line file instead of a surgery on app.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..models import (
    NormalizedJob, SearchQuery, JobSource, SourceTier, SourceCategory,
)
from ..http_client import HttpClient
from ..proxies import ProxyManager
from ..credits import CreditManager


class AdapterStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"                 # returned partial results
    SKIPPED_NO_CREDS = "skipped_no_creds"  # paid source, no API key configured
    SKIPPED_TIER = "skipped_tier"         # gated out for this user's role
    RATE_LIMITED = "rate_limited"
    NO_CREDITS = "no_credits"             # monthly cap hit
    UNAVAILABLE = "unavailable"           # circuit breaker open
    ERROR = "error"


@dataclass
class AdapterResult:
    source: JobSource
    status: AdapterStatus
    jobs: list[NormalizedJob] = field(default_factory=list)
    message: str = ""
    credits_used: int = 0

    @property
    def count(self) -> int:
        return len(self.jobs)


class SourceAdapter:
    """Base class. Subclasses set the class attrs and implement ``_fetch``."""

    # -- identity (override in subclasses) --
    name: JobSource = JobSource.UNKNOWN
    tier: SourceTier = SourceTier.FREE
    category: SourceCategory = SourceCategory.SEARCH
    requires_proxy: bool = False
    # env vars that MUST be present for a paid source to run; empty => always on
    required_env: tuple[str, ...] = ()
    # name used by the CreditManager (paid sources only)
    credit_key: Optional[str] = None

    def __init__(self, proxy_manager: Optional[ProxyManager] = None,
                 credit_manager: Optional[CreditManager] = None) -> None:
        self.proxies = proxy_manager
        self.credits = credit_manager
        self.http = HttpClient(
            proxy_manager=proxy_manager,
            use_proxy=self.requires_proxy,
        )

    # -- availability gating ------------------------------------------------

    def missing_env(self) -> list[str]:
        import os
        return [k for k in self.required_env if not (os.environ.get(k) or "").strip()]

    def is_configured(self) -> bool:
        return not self.missing_env()

    # -- main entry ---------------------------------------------------------

    def fetch(self, query: SearchQuery) -> AdapterResult:
        # paid source with no creds → skip cleanly (this is the "stub" behavior)
        if self.required_env and not self.is_configured():
            return AdapterResult(
                self.name, AdapterStatus.SKIPPED_NO_CREDS,
                message=f"missing env: {', '.join(self.missing_env())}")
        # credit / circuit-breaker gating for paid sources
        if self.credit_key and self.credits is not None:
            if not self.credits.is_available(self.credit_key):
                return AdapterResult(self.name, AdapterStatus.UNAVAILABLE,
                                     message="circuit breaker open")
            if not self.credits.can_spend(self.credit_key, 1):
                return AdapterResult(self.name, AdapterStatus.NO_CREDITS,
                                     message="monthly cap reached")
        try:
            jobs = self._fetch(query)
            if self.credit_key and self.credits is not None:
                self.credits.record(self.credit_key, 1)
                self.credits.report_success(self.credit_key)
            return AdapterResult(self.name, AdapterStatus.OK, jobs=jobs,
                                 credits_used=1 if self.credit_key else 0)
        except Exception as e:
            if self.credit_key and self.credits is not None:
                self.credits.report_failure(self.credit_key)
            return AdapterResult(self.name, AdapterStatus.ERROR,
                                 message=f"{type(e).__name__}: {e}")

    # -- subclasses implement this -----------------------------------------

    def _fetch(self, query: SearchQuery) -> list[NormalizedJob]:
        raise NotImplementedError

    # -- shared normalization helper ---------------------------------------

    def _mk(self, **kw) -> NormalizedJob:
        kw.setdefault("source", self.name)
        kw.setdefault("source_tier", self.tier)
        kw.setdefault("source_category", self.category)
        return NormalizedJob(**kw)
