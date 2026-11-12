"""plans.py - what each plan includes: the tier matrix the owner approved on
2026-11-05 ("Job Hunter - Plan tier matrix" doc), as data.

Every limit the app enforces is read from here, so changing a number is an
edit to this file, not a migration or a hunt through routes. `None` means
unlimited. Counting still happens for unlimited plans: the counters are the
usage data pricing is set from.

Admin is a ROLE, not a plan (entitlements.is_admin): it is never sold or
granted, and the ADMIN row below is what an admin gets whatever their plan
column says.

Decisions behind the numbers (matrix doc, 2026-11-05, and 2026-11-09):
- Beta (The owner, 2026-11-09 - replaces the 11-05 "no Beta plan" rule): every new
  account joins on NEW_USER_PLAN = "beta", which has Premium's limits and NO
  end date; the users who were on the 30-day Premium promotion moved to it too
  (migration 25). It stays until the admin moves someone. Not a paid plan.
- Beta shows EVERY match (The owner, 2026-11-11): a Premium copy capped Beta at 6
  per search and locked the rest behind "upgrade options open soon", which a
  beta user cannot act on (ticket from account 10). Beta = Premium with
  matches_per_search unlimited; migration 31 unlocked what was held back.
- Promotions (a plan with an end date, then Free) remain an admin tool; a web
  push goes out NOTICE_DAYS before one ends.
- Matches per search: Free 3, Premium 6, Expert all; the rest are shown as
  blurred placeholders whose content never leaves the server.
- Auto-apply is not sold while it is parked: admin only.
- Paid job sources are credit-capped; Expert's cap is higher than Premium's.
  The caps are placeholders until a paid source is configured (none is in
  production as of 2026-11-05) - tune them here.
- Billing (Step 11): monthly, or 6 months upfront at SIX_MONTH_DISCOUNT off.
- Daily picks (The owner, 2026-11-12, launch campaign): at most MATCHES_PER_DAY
  new matches reach a user per Israel day, best first. The rest wait, unseen
  and unmentioned (no blur), and come in on the following days - so a first
  search of 30 matches is a week of picks, not one day and then nothing.
  Admin is not capped.
"""
from __future__ import annotations

UNLIMITED = None

NEW_USER_PLAN = "beta"          # 2026-11-09: every signup, no end date
PROMOTION_PLAN = "premium"      # what the 11-05 new-user promotion granted
PROMOTION_DAYS = 30
NOTICE_DAYS = (7, 1)            # web push this many days before a promotion ends
SIX_MONTH_DISCOUNT = 0.20       # Step 11 (Paddle); not used until billing ships
MATCHES_PER_DAY = 7             # 2026-11-12: daily picks, every plan; admin unlimited

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
        "matches_per_day": MATCHES_PER_DAY,
        # Every plan may turn auto-apply on; each user decides in Settings and
        # it is off until they do (The owner, 2026-11-08). The engine's own switch
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
        "matches_per_day": MATCHES_PER_DAY,
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
    # "beta" is inserted just below: a copy of Premium under its own name.
    "expert": {
        "label": "Expert",
        "search_cadence": "daily",      # "daily + on demand"
        "on_demand_search": 10,
        "matches_per_search": UNLIMITED,
        "matches_per_day": MATCHES_PER_DAY,
        "auto_apply": True,             # every plan (2026-11-08)
        "cover_letter": 30,
        "cv_review": UNLIMITED,
        "tailored_cv": 30,
        "notifications": "all",
        "analytics": "full",
        "export": True,
        "paid_sources": True,
        "paid_source_credit": 200,      # higher than Premium (The owner, 2026-11-05)
        "history_days": UNLIMITED,
        "ai_calls_per_day": 600,
        "deferred_list": True,
    },
}

# Beta = Premium's limits under its own name (2026-11-09), so the beta users
# are countable and can be moved as a group later. Kept a copy, not a second
# literal, so the two can never drift apart by accident - except one deliberate
# override: Beta shows every match per search (2026-11-11, see the header).
PLANS = {**{k: v for k, v in PLANS.items() if k != "expert"},
         "beta": {**PLANS["premium"], "label": "Beta", "matches_per_search": UNLIMITED},
         "expert": PLANS["expert"]}

ADMIN = {
    "label": "Admin",
    "search_cadence": "daily",
    "on_demand_search": UNLIMITED,
    "matches_per_search": UNLIMITED,
    "matches_per_day": UNLIMITED,
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
