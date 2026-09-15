"""
entitlements.py - what a user's plan lets them do.

One module, because the alternative is `if role == "admin" or plan in (...)`
scattered across routes and a UI that disagrees with the server about who may
do what. Every gate in the app answers from here.

Deliberately small. Prices, billing periods, quotas and the provider are the
payments phase; this is only the question "may this user turn this on", which
the auto-apply toggle needs now.

The plans, from the product decision on record:
    free    - real value, but no auto-apply
    premium - auto-apply
    expert  - everything
    admin   - everything, plus the admin panel. Not a plan: a ROLE, checked
              separately, so Eran's own access never depends on a plan column
              someone has to remember to set.
"""

PLANS = ("free", "premium", "expert")
PAID_PLANS = ("premium", "expert")


def plan_of(user) -> str:
    """The user's plan, normalised. Anything unrecognised reads as 'free'.

    Fail CLOSED: an unknown or empty value must not open a paid feature. A
    typo in a plan name should cost someone a feature they can ask about, not
    silently hand out the thing that costs money to run.
    """
    if not user:
        return "free"
    raw = user.get("plan") if hasattr(user, "get") else user["plan"]
    plan = (raw or "").strip().lower()
    return plan if plan in PLANS else "free"


def is_admin(user) -> bool:
    if not user:
        return False
    role = (user.get("role") if hasattr(user, "get") else user["role"]) or ""
    return role.strip().lower() == "admin"


def can_auto_apply(user) -> bool:
    """May this user have the app apply on their behalf without asking?

    Admin always may. Otherwise it takes a paid plan. Note this answers only
    "is it allowed to be ON" - whether the engine then does anything is a
    separate switch (APPLY_ENGINE_ENABLED), and the user's own toggle is a
    third. All three have to agree before a form is ever submitted.
    """
    return is_admin(user) or plan_of(user) in PAID_PLANS
