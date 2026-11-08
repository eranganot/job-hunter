"""plans.py - what each plan includes: the tier matrix Eran approved on
2026-11-05 ("Job Hunter - Plan tier matrix" doc), as data.

Every limit the app enforces is read from here, so changing a number is an
edit to this file, not a migration or a hunt through routes. `None` means
unlimited. Counting still happens for unlimited plans: the counters are the
usage data pricing is set from.

Admin is a ROLE, not a plan (entitlements.is_admin): it is never sold or
granted, and the ADMIN row below is what an admin gets whatever their plan
column says.

Decisions behind the numbers (matrix doc, 2026-11-05):
- No Beta plan. New users, and existing users when enforcement ships, get
  Premium free for PROMOTION_DAYS, then move to Free automatically.
- Users get a web push NOTICE_DAYS before a promotion ends.
- Matches per search: Free 3, Premium 6, Expert all; the rest are shown as
  blurred placeholders whose content never leaves the server.
- Auto-apply is not sold while it is parked: admin only.
- Paid job sources are credit-capped; Expert's cap is higher than Premium's.
  The caps are placeholders until a paid source is configured (none is in
  production as of 2026-11-05) - tune them here.
- Billing (Step 11): monthly, or 6 months upfront at SIX_MONTH_DISCOUNT off.
"""
from __future__ import annotations

UNLIMITED = None

PROMOTION_PLAN = "premium"
PROMOTION_DAYS = 30
NOTICE_DAYS = (7, 1)            # web push this many days before a promotion ends
SIX_MONTH_DISCOUNT = 0.20       # Step 11 (Paddle); not used until billing ships

# meter -> the period its count resets on
METERS = {
    "on_demand_search": "day",      # a search the user starts (not the scheduled one)
    "cover_letter": "month",
    "cv_review": "month",           # the CV optimizer: general review, score + tips
    "tailored_cv": "month",         # Step 14: the CV rewritten for one job
    "paid_source_credit": "month",  # one call to a paid job source
}

PLANS = {
    "free": {
        "label": "Free",
        "search_cadence": "weekly",
        "on_demand_search": 1,
        "matches_per_search": 3,
        # Every plan may turn auto-apply on; each user decides in Settings and
        # it is off until they do (Eran, 2026-11-08). The engine's own switch
        # (APPLY_ENGINE_ENABLED) still has to be on before anything is sent.
        "auto_apply": True,
        "cover_letter": 0,
        "cv_review": 2,
        "tailored_cv": 2,
        "notifications": "all",
        "analytics": "basic",
        "export": False,
        "paid_sources": False,
        "paid_source_credit": 0,
        "history_days": 30,
        "ai_calls_per_day": 30,
        "deferred_list": False,
    },
    "premium": {
        "label": "Premium",
        "search_cadence": "daily",
        "on_demand_search": 3,
        "matches_per_search": 6,
        "auto_apply": True,             # every plan (2026-11-08)
        "cover_letter": 10,
        "cv_review": 4,
        "tailored_cv": 10,
        "notifications": "all",
        "analytics": "full",
        "export": False,
        "paid_sources": True,
        "paid_source_credit": 50,       # placeholder cap - no paid source configured yet
        "history_days": 365,
        "ai_calls_per_day": 300,
        "deferred_list": True,
    },
    "expert": {
        "label": "Expert",
        "search_cadence": "daily",      # "daily + on demand"
        "on_demand_search": 10,
        "matches_per_search": UNLIMITED,
        "auto_apply": True,             # every plan (2026-11-08)
        "cover_letter": 30,
        "cv_review": UNLIMITED,
        "tailored_cv": 30,
        "notifications": "all",
        "analytics": "full",
        "export": True,
        "paid_sources": True,
        "paid_source_credit": 200,      # higher than Premium (Eran, 2026-11-05)
        "history_days": UNLIMITED,
        "ai_calls_per_day": 600,
        "deferred_list": True,
    },
}

ADMIN = {
    "label": "Admin",
    "search_cadence": "daily",
    "on_demand_search": UNLIMITED,
    "matches_per_search": UNLIMITED,
    "auto_apply": True,                 # "when on" - the engine has its own switch
    "cover_letter": UNLIMITED,
    "cv_review": UNLIMITED,
    "tailored_cv": UNLIMITED,
    "notifications": "all",
    "analytics": "full",
    "export": True,
    "paid_sources": True,
    "paid_source_credit": UNLIMITED,
    "history_days": UNLIMITED,
    "ai_calls_per_day": UNLIMITED,      # the global ceiling still applies
    "deferred_list": True,
}

PLAN_NAMES = tuple(PLANS)
PAID_PLANS = ("premium", "expert")


def row(tier: str) -> dict:
    """The matrix row for a plan name or 'admin'. Unknown names read as Free."""
    if tier == "admin":
        return ADMIN
    return PLANS.get(tier) or PLANS["free"]
