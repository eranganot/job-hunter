"""Phase 5a (Step 4): plans, usage counters and promotions.

The numbers are the tier matrix Eran approved on 2026-11-05; the promotion
rules are his decisions from the same day: every new account starts on Premium
free for 30 days, then Free; a web push 7 days and 1 day before; admin is a
role, never a plan."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import entitlements
import migrations
import plans
from tests.test_routes import Client, stack, users  # noqa: F401

NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migrations.run(c)
    c.execute("INSERT INTO users (id, name, email, password_hash, salt, created_date, role) "
              "VALUES (1,'u','u@x.test','h','s','2026-11-01','user')")
    c.commit()
    yield c
    c.close()


def _user(conn, uid=1):
    return dict(conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())


# ── the approved matrix ────────────────────────────────────────────────────

def test_the_matrix_is_the_one_eran_approved():
    f, p, e = plans.PLANS["free"], plans.PLANS["premium"], plans.PLANS["expert"]
    assert (f["matches_per_search"], p["matches_per_search"], e["matches_per_search"]) == (3, 6, None)
    assert (f["on_demand_search"], p["on_demand_search"], e["on_demand_search"]) == (1, 3, 10)
    assert (f["cover_letter"], p["cover_letter"], e["cover_letter"]) == (0, 10, 30)
    assert (f["cv_review"], p["cv_review"], e["cv_review"]) == (2, 4, None)
    assert (f["ai_calls_per_day"], p["ai_calls_per_day"], e["ai_calls_per_day"]) == (30, 300, 600)
    assert (f["search_cadence"], p["search_cadence"]) == ("weekly", "daily")
    assert (f["history_days"], p["history_days"], e["history_days"]) == (30, 365, None)
    assert not f["paid_sources"] and e["paid_source_credit"] > p["paid_source_credit"] > 0
    assert not f["deferred_list"] and p["deferred_list"] and e["deferred_list"]
    assert all(row["auto_apply"] for row in plans.PLANS.values())   # every plan, user's choice (2026-11-08)
    assert (plans.PROMOTION_PLAN, plans.PROMOTION_DAYS, plans.NOTICE_DAYS) == ("premium", 30, (7, 1))
    assert plans.SIX_MONTH_DISCOUNT == 0.20
    assert "admin" not in plans.PLANS                                    # admin is a role
    # Beta (2026-11-09): Premium's limits under its own name, the plan every signup gets.
    assert plans.NEW_USER_PLAN == "beta" and "beta" not in plans.PAID_PLANS
    assert {k: v for k, v in plans.PLANS["beta"].items() if k != "label"} == \
        {k: v for k, v in p.items() if k != "label"} and plans.PLANS["beta"]["label"] == "Beta"


def test_admin_is_a_role_with_no_limits_whatever_the_plan_column_says():
    admin = {"role": "admin", "plan": "free"}
    assert entitlements.tier_of(admin) == "admin"
    assert entitlements.limit(admin, "matches_per_search") is None
    assert entitlements.can_auto_apply(admin)


# ── promotions ─────────────────────────────────────────────────────────────

def test_a_promotion_is_over_at_its_end_even_before_the_sweep_runs():
    u = {"role": "user", "plan": "premium", "plan_until": "2026-11-05 11:59:59", "plan_after": "free"}
    assert entitlements.plan_of(u, NOW) == "free"                       # fails closed
    assert entitlements.plan_of(u, NOW - timedelta(minutes=5)) == "premium"
    u["plan_after"] = "gold"                                             # unknown fallback
    assert entitlements.plan_of(u, NOW) == "free"


def _promo(conn, uid=1, now=NOW):
    """The 30-day Premium promotion new users got from 11-05 to 11-09; still
    what an admin-granted promotion looks like."""
    return entitlements.grant_plan(conn, uid, plans.PROMOTION_PLAN, days=plans.PROMOTION_DAYS,
                                   after="free", reason="new_user", now=now)


def test_a_new_user_joins_beta_with_no_end_date(conn):
    out = entitlements.start_new_user_plan(conn, 1, now=NOW)
    assert out == {"plan": "beta", "plan_until": None, "plan_after": None, "plan_source": "beta"}
    u = _user(conn)
    assert entitlements.plan_of(u, NOW + timedelta(days=3650)) == "beta"   # never runs out
    assert entitlements.limit(u, "matches_per_search") == 6                 # Premium's limits
    assert entitlements.plan_info(u, NOW)["label"] == "Beta"
    assert entitlements.expire_promotions(conn, NOW + timedelta(days=60)) == []
    assert entitlements.due_notices(conn, NOW + timedelta(days=23)) == []    # no downgrade push
    ev = conn.execute("SELECT from_plan, to_plan, reason FROM plan_events").fetchall()
    assert [tuple(r) for r in ev] == [("free", "beta", "beta")]


def test_running_promotions_move_to_beta_and_nothing_else_does(conn):
    for uid, email, role in ((2, "b@x.test", "user"), (3, "c@x.test", "user"),
                             (4, "d@x.test", "user"), (5, "adm@x.test", "admin")):
        conn.execute("INSERT INTO users (id, name, email, password_hash, salt, created_date, role) "
                     "VALUES (?,?,?,'h','s','2026-11-01',?)", (uid, email, email, role))
    _promo(conn, 1)                                                    # running -> beta
    entitlements.grant_plan(conn, 2, "premium", days=3, now=NOW - timedelta(days=10))   # ended -> left
    entitlements.expire_promotions(conn, NOW)
    entitlements.grant_plan(conn, 3, "expert", now=NOW)                # admin-set, permanent -> left
    entitlements.grant_plan(conn, 4, "expert", days=14, now=NOW)       # Expert promotion -> left (Beta would be a downgrade)
    _promo(conn, 5)                                                    # admin role -> left
    moved = entitlements.move_promotions_to_beta(conn, NOW + timedelta(hours=1))
    assert moved == [1]
    plans_now = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
        "SELECT id, plan, plan_until, plan_source FROM users")}
    assert plans_now[1] == ("beta", None, "beta_migration")
    assert plans_now[2][0] == "free" and plans_now[3] == ("expert", None, "admin")
    assert plans_now[4][:2] == ("expert", "2026-11-19 12:00:00") and plans_now[5][0] == "premium"
    assert entitlements.move_promotions_to_beta(conn, NOW + timedelta(hours=2)) == []    # idempotent


def test_admin_can_grant_a_permanent_plan_and_it_is_recorded(conn):
    entitlements.grant_plan(conn, 1, "expert", by_user_id=9, now=NOW)
    u = _user(conn)
    assert (u["plan"], u["plan_until"], u["plan_after"]) == ("expert", None, None)
    assert conn.execute("SELECT by_user_id FROM plan_events").fetchone()[0] == 9
    with pytest.raises(ValueError):
        entitlements.grant_plan(conn, 1, "gold", now=NOW)               # not a plan


def test_ended_promotions_move_to_their_fallback_once(conn):
    _promo(conn, 1)
    assert entitlements.expire_promotions(conn, NOW + timedelta(days=29)) == []
    moved = entitlements.expire_promotions(conn, NOW + timedelta(days=30))
    assert moved == [(1, "premium", "free")]
    u = _user(conn)
    assert (u["plan"], u["plan_until"], u["plan_source"]) == ("free", None, "promotion_ended")
    assert entitlements.expire_promotions(conn, NOW + timedelta(days=31)) == []     # idempotent


def test_notices_come_7_days_and_1_day_before_and_never_twice(conn):
    _promo(conn, 1)            # ends 2026-12-05 12:00
    pushed = []
    run = lambda at: entitlements.run_lifecycle(lambda: _NoClose(conn), lambda u, m: pushed.append((u, m)), at)

    run(NOW + timedelta(days=22))                                        # 8 days left: nothing
    assert pushed == []
    run(NOW + timedelta(days=23))                                        # 7 days left
    run(NOW + timedelta(days=23, hours=1))                               # an hour later: no repeat
    assert len(pushed) == 1 and "in 7 days (5 Dec)" in pushed[0][1] and "Free plan" in pushed[0][1]
    run(NOW + timedelta(days=29, hours=1))                               # under a day left
    assert len(pushed) == 2 and "tomorrow" in pushed[1][1]
    out = run(NOW + timedelta(days=30))                                  # ended
    assert len(pushed) == 2 and out["moved"] == [(1, "premium", "free")]


def test_a_short_promotion_is_told_its_real_end_then_tomorrow(conn):
    """A 3-day promotion is inside the 7-day window from the start: one notice
    with the real count ("in 3 days"), then the 1-day one - never a 7-day text."""
    entitlements.grant_plan(conn, 1, "premium", days=3, now=NOW)
    pushed = []
    sweep = lambda at: entitlements.run_lifecycle(lambda: _NoClose(conn), lambda u, m: pushed.append(m), at)
    sweep(NOW)
    sweep(NOW + timedelta(hours=1))
    assert len(pushed) == 1 and "in 3 days" in pushed[0]
    sweep(NOW + timedelta(days=2, hours=1))
    assert len(pushed) == 2 and "tomorrow" in pushed[1]


def test_a_promotion_granted_inside_its_last_day_gets_only_the_1_day_notice(conn):
    entitlements.grant_plan(conn, 1, "premium", days=1, now=NOW)
    pushed = []
    entitlements.run_lifecycle(lambda: _NoClose(conn), lambda u, m: pushed.append(m), NOW + timedelta(hours=2))
    entitlements.run_lifecycle(lambda: _NoClose(conn), lambda u, m: pushed.append(m), NOW + timedelta(hours=3))
    assert pushed and len(pushed) == 1 and "tomorrow" in pushed[0]


def test_a_failed_push_is_logged_and_not_retried(conn, capsys):
    _promo(conn, 1)
    def boom(u, m):
        raise RuntimeError("no subscription")
    entitlements.run_lifecycle(lambda: _NoClose(conn), boom, NOW + timedelta(days=23))
    assert "promotion notice push failed" in capsys.readouterr().out
    assert entitlements.due_notices(conn, NOW + timedelta(days=23)) == []


class _NoClose:
    """run_lifecycle closes the connection it opens; the fixture owns this one."""
    def __init__(self, c):
        self._c = c

    def __getattr__(self, name):
        return getattr(self._c, name)

    def close(self):
        pass


# ── usage counters ─────────────────────────────────────────────────────────

def test_usage_is_counted_and_refused_over_the_plan_limit(conn):
    free = {"id": 1, "role": "user", "plan": "free"}
    assert entitlements.try_consume(conn, free, "on_demand_search", now=NOW) == (True, 1, 1)
    assert entitlements.try_consume(conn, free, "on_demand_search", now=NOW) == (False, 1, 1)
    assert entitlements.used(conn, 1, "on_demand_search", NOW) == 1      # a refusal is not counted
    tomorrow = NOW + timedelta(days=1)
    assert entitlements.try_consume(conn, free, "on_demand_search", now=tomorrow)[0]


def test_unlimited_plans_are_still_counted():
    c = sqlite3.connect(":memory:")
    migrations.run(c)
    expert = {"id": 7, "role": "user", "plan": "expert"}
    for _ in range(3):
        assert entitlements.try_consume(c, expert, "cv_review", now=NOW)[0]
    assert entitlements.used(c, 7, "cv_review", NOW) == 3


def test_a_day_is_an_israeli_day_and_a_month_a_calendar_month():
    late = datetime(2026, 11, 5, 22, 30, tzinfo=timezone.utc)          # 00:30 on 6 Nov in Israel
    assert entitlements.period_key("on_demand_search", late) == "2026-11-06"
    assert entitlements.period_key("cover_letter", late) == "2026-11"


# ── over HTTP ──────────────────────────────────────────────────────────────

def test_a_new_signup_starts_on_beta_and_admin_on_none(stack, users):  # noqa: F811
    db = stack["db"]
    conn = db.get_db()
    alice = dict(conn.execute("SELECT * FROM users WHERE email='alice@example.test'").fetchone())
    admin = dict(conn.execute("SELECT * FROM users WHERE email='admin@example.test'").fetchone())
    conn.close()
    assert (alice["plan"], alice["plan_until"], alice["plan_after"], alice["plan_source"]) == \
        ("beta", None, None, "beta")
    assert admin["plan_source"] is None


def test_me_reports_plan_limits_and_usage(stack, users):  # noqa: F811
    status, _loc, body = users["b"].get("/api/me")
    assert status == 200
    me = json.loads(body)
    info = me["plan_info"]
    assert info["tier"] in ("beta", "free") and info["limits"]["matches_per_search"] in (3, 6)
    assert set(me["usage"]) == set(plans.METERS)
    assert me["usage"]["cover_letter"]["period"] == "month"
    status, _loc, body = users["admin"].get("/api/me")
    assert json.loads(body)["plan_info"]["tier"] == "admin"
