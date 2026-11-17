"""
costreport.py - what running Job Hunter costs, per active user (GTM row 10a).

The owner, 2026-11-16: "I would like to see the running cost per active user
till now". Two numbers will sit on Admin -> Today:
  * running cost per active user (this module) - what the product costs to run;
  * paid cost per activated user - ad spend, read from Meta once ads start.
Kept apart so a rise in one cannot hide in the other.

What is counted, and where each number comes from:
  * Gemini - every call is in the llm_usage ledger (prompt and total tokens).
    Priced here at Google's published paid-tier rates. Output is billed
    INCLUDING thinking tokens, so billable output = total - prompt, not the
    candidates count. A model missing from PRICES is priced as Flash and named
    in `unpriced`, so a model change shows up instead of costing $0.
  * Hosting (Railway) - not visible from inside the app. JH_HOSTING_USD_MONTH
    (Railway env) is the monthly bill; spread evenly over the window. Unset =
    reported as not set, never guessed.
  * Paid job sources - each runs on a free monthly allowance (paidpool.py), so
    $0 while every one stays inside it; a source past its allowance is named.

Per active user per day = the window's cost / active user-days (a person with
at least one decision on a day counts once for that day).

Active user = a person (the test account excluded, the admin included - his
searches are in the cost too) who approved or passed on at least one job in
the window. A user whose daily search ran but who never looked is a cost, not
an active user.
"""
import os
from datetime import datetime, timedelta, timezone

# USD per 1M tokens: (input, output incl. thinking). Google AI Studio paid
# tier list prices, checked 2026-11-16 (ai.google.dev/gemini-api/docs/pricing).
PRICES = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-pro": (1.25, 10.00),
}
FALLBACK = "gemini-2.5-flash"
DECISIONS = ("job_approved", "job_rejected", "bulk_approve", "bulk_reject")


def _price(model):
    m = str(model or "").strip().lower()
    for name in sorted(PRICES, key=len, reverse=True):      # "flash-lite" before "flash"
        if m == name or m.startswith(name + "-"):
            return PRICES[name], True
    return PRICES[FALLBACK], False


def gemini_usd(prompt_tokens, total_tokens, model=FALLBACK):
    (pin, pout), _known = _price(model)
    out = max(int(total_tokens or 0) - int(prompt_tokens or 0), 0)
    return int(prompt_tokens or 0) / 1e6 * pin + out / 1e6 * pout


def hosting_month_usd():
    raw = os.environ.get("JH_HOSTING_USD_MONTH", "").strip()
    try:
        v = float(raw) if raw else None
    except ValueError:
        return None
    return v if v is not None and v >= 0 else None


def report(conn, days=30, now=None, test_user_ids=(), paid_sources=None):
    now = now or datetime.now(timezone.utc)
    days = max(1, min(int(days or 30), 90))                 # the ledger keeps 90 days
    since_day = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    since_ts = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    test_ids = {int(i) for i in test_user_ids if i is not None}

    gem = {"calls": 0, "prompt_tokens": 0, "output_tokens": 0, "usd": 0.0, "test_account_usd": 0.0,
           "first_day": None, "by_purpose": {}, "unpriced": []}
    rows = conn.execute(
        "SELECT model, purpose, user_id, MIN(day) AS first_day, COALESCE(SUM(calls),0) AS calls, "
        "COALESCE(SUM(prompt_tokens),0) AS p, COALESCE(SUM(total_tokens),0) AS t "
        "FROM llm_usage WHERE day >= ? GROUP BY model, purpose, user_id", (since_day,)).fetchall()
    for r in rows:
        usd = gemini_usd(r["p"], r["t"], r["model"])
        if not _price(r["model"])[1] and r["model"] not in gem["unpriced"]:
            gem["unpriced"].append(r["model"])
        gem["calls"] += int(r["calls"])
        gem["prompt_tokens"] += int(r["p"])
        gem["output_tokens"] += max(int(r["t"]) - int(r["p"]), 0)
        gem["usd"] += usd
        if r["user_id"] is not None and int(r["user_id"]) in test_ids:
            gem["test_account_usd"] += usd
        gem["by_purpose"][r["purpose"]] = gem["by_purpose"].get(r["purpose"], 0.0) + usd
        if r["first_day"] and (gem["first_day"] is None or r["first_day"] < gem["first_day"]):
            gem["first_day"] = r["first_day"]
    gem["by_purpose"] = [{"purpose": k, "usd": round(v, 2)}
                         for k, v in sorted(gem["by_purpose"].items(), key=lambda kv: -kv[1])][:6]
    gem["usd"] = round(gem["usd"], 2)
    gem["test_account_usd"] = round(gem["test_account_usd"], 2)

    month = hosting_month_usd()
    hosting = {"set": month is not None, "month_usd": month,
               "usd": round(month * days / 30.0, 2) if month is not None else None}

    over = [s.get("label") or k for k, s in (paid_sources or {}).items()
            if int(s.get("month_used") or 0) > int(s.get("free_allowance") or 0)]
    sources = {"usd": 0.0 if not over else None, "over_allowance": over}

    marks = ",".join("?" for _ in DECISIONS)
    act = conn.execute(
        "SELECT a.user_id, u.role FROM activity_log a JOIN users u ON u.id = a.user_id AND COALESCE(u.is_active, 1)=1 "
        "WHERE a.event_type IN (%s) AND a.created_date >= ? GROUP BY a.user_id, u.role" % marks,
        (*DECISIONS, since_ts)).fetchall()
    people = [r for r in act if int(r["user_id"]) not in test_ids]
    active = len(people)
    # Active user-days: each (person, day) with a decision. Cost per active user
    # per day = the window's cost / these (GTM row 12): what one engaged day costs.
    user_days = sum(1 for r in conn.execute(
        "SELECT a.user_id, SUBSTR(a.created_date,1,10) FROM activity_log a JOIN users u ON u.id = a.user_id "
        "AND COALESCE(u.is_active, 1)=1 WHERE a.event_type IN (%s) AND a.created_date >= ? "
        "GROUP BY a.user_id, SUBSTR(a.created_date,1,10)" % marks, (*DECISIONS, since_ts)).fetchall()
        if int(r[0]) not in test_ids)
    active_admins = sum(1 for r in people if str(r["role"] or "") == "admin")

    total = gem["usd"] + (hosting["usd"] or 0.0) + (sources["usd"] or 0.0)
    return {
        "days": days, "since": since_day,
        "gemini": gem, "hosting": hosting, "paid_sources": sources,
        "total_usd": round(total, 2),
        "complete": hosting["set"] and sources["usd"] is not None,
        "active_users": active, "active_admins": active_admins,
        "per_active_user_usd": round(total / active, 2) if active else None,
        "active_user_days": user_days,
        "per_active_user_day_usd": round(total / user_days, 3) if user_days else None,
    }
