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
    MicrosoftAdapter, GoogleAdapter, MetaAdapter, AmazonAdapter, AppleAdapter,
    TheirStackAdapter, AdzunaAdapter, CoresignalAdapter,
    ApifyAdapter, JobSpyAdapter,
)

# Every adapter class the pipeline knows about.
ALL_ADAPTERS: list[type[SourceAdapter]] = [
    # FREE — Big Tech (the existing ATS/board sources still live in app.py)
    MicrosoftAdapter, GoogleAdapter, MetaAdapter, AmazonAdapter, AppleAdapter,
    # PAID — aggregators
    TheirStackAdapter, AdzunaAdapter, CoresignalAdapter,
    # PAID — managed scraping
    ApifyAdapter, JobSpyAdapter,
]


def is_admin(role: Optional[str]) -> bool:
    return (role or "").strip().lower() == "admin"


def build_adapters(
    role: Optional[str],
    proxy_manager: Optional[ProxyManager] = None,
    credit_manager: Optional[CreditManager] = None,
) -> list[SourceAdapter]:
    """
    Instantiate the adapters this user is allowed to run.

    FREE tier → everyone. PAID tier → admin only. Proxies (a paid resource) are
    only handed to adapters on admin runs; free users hit Big-Tech endpoints
    directly (best-effort, no proxy spend on the admin's account).
    """
    admin = is_admin(role)
    proxies = proxy_manager if admin else None
    out: list[SourceAdapter] = []
    for cls in ALL_ADAPTERS:
        if cls.tier == SourceTier.PAID and not admin:
            continue                      # gated out — never instantiated
        out.append(cls(proxy_manager=proxies, credit_manager=credit_manager))
    return out
