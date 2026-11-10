"""Step 12: does the learning work? (2026-11-07)

Read-only. Over the jobs a user DECIDED on - approved (incl. applied) versus
passed by the user - it answers:
  * are demoted jobs (feedback_penalty > 0) approved less than the others?
  * which demotion reason earns its place (company / title / location / salary...)?
  * does the AI match score predict approval?
  * and are approved jobs applied to, by the same cuts?
"Already applied elsewhere" counts as approved and applied (The owner, 2026-11-05).

Caveat, shown with the report: a passed job keeps the penalty it had when he
passed it (the live recompute runs only on open jobs) - the right moment; an
approved job's penalty keeps being recomputed while it is open, so it can
drift from what it was at decision time.
"""
from __future__ import annotations

SCORE_BUCKETS = ((85, "85+"), (70, "70-84"), (50, "50-69"), (0, "under 50"))


def _bucket(score):
    if score is None:
        return "no score"
    for lo, name in SCORE_BUCKETS:
        if score >= lo:
            return name
    return "under 50"


def _rate(a, b):
    return round(100.0 * a / b, 1) if b else None


def _group():
    return {"decided": 0, "approved": 0, "applied": 0}


def report(conn, user_id=None) -> dict:
    sql = ("SELECT status, rejected_by, notes, COALESCE(feedback_penalty,0) AS pen, "
           "COALESCE(feedback_reason,'') AS why, match_score FROM jobs "
           "WHERE (status IN ('approved','applied') OR (status='rejected' AND rejected_by='user'))")
    args = ()
    if user_id is not None:
        sql += " AND user_id=?"
        args = (user_id,)
    rows = conn.execute(sql, args).fetchall()

    by_pen, by_reason, by_score = {}, {}, {}
    total = _group()
    for r in rows:
        elsewhere = r["status"] == "rejected" and "applied elsewhere" in (r["notes"] or "").lower()
        yes = r["status"] in ("approved", "applied") or elsewhere
        applied = r["status"] == "applied" or elsewhere
        pen = int(r["pen"] or 0)
        keys = [(by_pen, "demoted" if pen > 0 else "not demoted"),
                (by_reason, (r["why"] or "").strip() if pen > 0 else "(not demoted)"),
                (by_score, _bucket(r["match_score"]))]
        for g in [total] + [d.setdefault(k, _group()) for d, k in keys]:
            g["decided"] += 1
            g["approved"] += 1 if yes else 0
            g["applied"] += 1 if applied else 0

    def finish(d):
        out = []
        for name, g in d.items():
            out.append({"group": name, "decided": g["decided"], "approved": g["approved"],
                        "approval_rate": _rate(g["approved"], g["decided"]),
                        "applied": g["applied"], "apply_rate": _rate(g["applied"], g["approved"]),
                        "small_sample": g["decided"] < 20})
        return sorted(out, key=lambda x: -x["decided"])

    pen = {x["group"]: x for x in finish(by_pen)}
    a, b = pen.get("not demoted"), pen.get("demoted")
    if not b or b["decided"] < 20:
        verdict = "Not enough demoted jobs decided yet to judge the penalties (need 20)."
    elif a and a["approval_rate"] is not None and b["approval_rate"] is not None:
        gap = a["approval_rate"] - b["approval_rate"]
        verdict = ("Penalties work: demoted jobs are approved %.0f points less often." % gap if gap >= 10 else
                   "Penalties barely matter: demoted jobs are approved only %.0f points less often." % gap if gap >= 0 else
                   "Penalties point the wrong way: demoted jobs are approved MORE often (%.0f points)." % -gap)
    else:
        verdict = "No undemoted decisions to compare against."
    order = [n for _, n in SCORE_BUCKETS] + ["no score"]
    score_rows = sorted(finish(by_score), key=lambda x: order.index(x["group"]) if x["group"] in order else 99)
    return {
        "scope": "all users" if user_id is None else "user %s" % user_id,
        "decided": total["decided"], "approved": total["approved"],
        "approval_rate": _rate(total["approved"], total["decided"]),
        "apply_rate": _rate(total["applied"], total["approved"]),
        "verdict": verdict,
        "by_penalty": finish(by_pen),
        "by_reason": finish(by_reason),
        "by_score": score_rows,
        "caveat": "A passed job keeps the penalty it had when passed; an approved job's penalty "
                  "is recomputed while it is open, so it can drift from decision time.",
    }
