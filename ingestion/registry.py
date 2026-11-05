"""
Adapter registry + role-based free/paid gating.

This is where requirement #4 ("free resources for everyone, paid only for the
admin") is enforced, in ONE place:

    FREE  adapters  -> every user
    PAID  adapters  -> only when role == "admin"

So a non-admin run never even instantiates a paid source, let alone spends a
credit on it.
"""
from __future__ import annotations

from typing import Optional

from .models import SourceTier
from .proxies import ProxyManager
from .credits import CreditManager
from .adapters import (
    SourceAdapter,
    MicrosoftAdapter, GoogleAdapter, AmazonAdapter, AppleAdapter,
    TheirStackAdapter, AdzunaAdapter, CoresignalAdapter,
    ApifyAdapter, JobSpyAdapter,
    SecretJobsAdapter,
)

# Every adapter class the pipeline knows about.
ALL_ADAPTERS: list[type[SourceAdapter]] = [
    # FREE — Big Tech (the existing ATS/board sources still live in app.py)
    MicrosoftAdapter, GoogleAdapter, AmazonAdapter, AppleAdapter,
    # PAID — aggregators
    TheirStackAdapter, AdzunaAdapter, CoresignalAdapter,
    # PAID — managed scraping
    ApifyAdapter, JobSpyAdapter,
    # PAID — authenticated (account holder's own session)
    SecretJobsAdapter,
]


def is_admin(role: Optional[str]) -> bool:
    return (role or "").strip().lower() == "admin"


def build_adapters(
    role: Optional[str],
    proxy_manager: Optional[ProxyManager] = None,
    credit_manager: Optional[CreditManager] = None,
    allow_paid: bool = False,
) -> list[SourceAdapter]:
    """
    Instantiate the adapters this user is allowed to run.

    FREE tier → everyone. PAID tier → admin, or a plan that includes paid
    sources with credit left this month (`allow_paid`, decided by the caller
    from plans.py - Premium and Expert, credit-capped). Proxies (a paid
    resource) are only handed to adapters on admin runs; everyone else hits
    endpoints directly (best-effort, no proxy spend on the admin's account).
    """
    admin = is_admin(role)
    proxies = proxy_manager if admin else None
    paid_ok = admin or bool(allow_paid)
    out: list[SourceAdapter] = []
    for cls in ALL_ADAPTERS:
        if cls.tier == SourceTier.PAID and not paid_ok:
            continue                      # gated out — never instantiated
        out.append(cls(proxy_manager=proxies, credit_manager=credit_manager))
    return out
