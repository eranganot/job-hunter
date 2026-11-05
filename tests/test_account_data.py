"""Step 6: export my data, and delete my account (14-day grace).

The deletion test is the important one: after the purge, NO table holding a
user_id may still have a row for that user - checked against every such table
the schema has, not a hand-written list, so a table added later is covered."""
import io
import json
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

import accountdata
import migrations
from tests.test_routes import Client, stack, users  # noqa: F401

PW = "correct-horse-1"


def _register(stack, email):  # noqa: F811
    import ratelimit
    ratelimit.reset_all()
    c = Client(stack["port"])
    st, loc, _ = c.post_form("/register", {"name": email.split("@")[0], "email": email,
                                           "password": PW, "password2": PW})
    assert st == 302, st
    return c


def _uid(db, email):
    c = db.get_db()
    try:
        return c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()[0]
    finally:
        c.close()


def _seed(db, uid):
    c = db.get_db()
    try:
        for st in ("new", "applied", "locked"):
            c.execute("INSERT INTO jobs (user_id,title,company,url,status,found_date) VALUES (?,?,?,?,?,?)",
                      (uid, "PM " + st, "Co " + st, "https://x.example/%s/%d" % (st, uid), st, "2026-11-05"))
        c.execute("INSERT INTO llm_usage (day,user_id,purpose,model) VALUES ('2026-11-05',?,'match_score','m')", (uid,))
        c.execute("INSERT INTO usage_counters (user_id,meter,period,uses) VALUES (?,?,?,?)", (uid, "cv_review", "2026-11", 1))
        c.execute("INSERT INTO user_blocklist (user_id, company_name) VALUES (?,?)", (uid, "Evil Co"))
        c.execute("UPDATE user_profiles SET telegram_token='SECRET-TOKEN' WHERE user_id=?", (uid,))
        c.commit()
    finally:
        c.close()


# ── export ─────────────────────────────────────────────────────────────────

def test_export_is_a_zip_of_everything_but_secrets_and_held_back_matches(stack):  # noqa: F811
    c = _register(stack, "export@example.test")
    uid = _uid(stack["db"], "export@example.test")
    _seed(stack["db"], uid)
    st, _l, body = c.get("/api/me/export")
    assert st == 200
    z = zipfile.ZipFile(io.BytesIO(body))
    assert {"data.json", "README.txt"} <= set(z.namelist())
    raw = z.read("data.json").decode()
    data = json.loads(raw)
    assert data["user"]["email"] == "export@example.test"
    titles = {j["title"] for j in data["tables"]["jobs"]}
    assert titles == {"PM new", "PM applied"}                               # the locked one is withheld
    assert data["held_back_matches"]["count"] == 1
    assert data["tables"]["user_blocklist"][0]["company_name"] == "Evil Co"
    for secret in ("SECRET-TOKEN", "password_hash", '"salt"', "PM locked", "Co locked"):
        assert secret not in raw, secret
    assert "sessions" not in data["tables"]


def test_export_needs_a_session(stack):  # noqa: F811
    st, loc, _b = Client(stack["port"]).get("/api/me/export")
    assert st in (302, 401)


# ── deletion over HTTP ─────────────────────────────────────────────────────

def test_deletion_needs_the_email_and_is_never_for_admin(stack, users):  # noqa: F811
    c = _register(stack, "careful@example.test")
    st, _l, body = c.post_json("/api/account/delete", {"confirm_email": "someone@else.test"})
    assert st == 400 and json.loads(body)["code"] == "confirm_email_mismatch"
    st, _l, body = users["admin"].post_json("/api/account/delete", {"confirm_email": "admin@example.test"})
    assert st == 400 and json.loads(body)["code"] == "admin_cannot_delete"


def test_a_request_signs_out_everywhere_and_signing_in_cancels_it(stack):  # noqa: F811
    db = stack["db"]
    c = _register(stack, "leaver@example.test")
    uid = _uid(db, "leaver@example.test")
    st, _l, body = c.post_json("/api/account/delete", {"confirm_email": "Leaver@Example.test"})
    out = json.loads(body)
    assert st == 200 and out["grace_days"] == 14 and out["delete_after"]
    conn = db.get_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (uid,)).fetchone()[0] == 0
        assert conn.execute("SELECT delete_after FROM users WHERE id=?", (uid,)).fetchone()[0] == out["delete_after"]
    finally:
        conn.close()
    assert c.get("/api/me")[0] in (302, 401)                                 # the old session is gone
    st, _l, body = Client(stack["port"]).get("/login?deleted=1")
    assert b"deleted in 14 days" in body
    c2 = Client(stack["port"])
    assert c2.post_form("/login", {"email": "leaver@example.test", "password": PW})[0] == 302
    conn = db.get_db()
    try:
        assert conn.execute("SELECT delete_after FROM users WHERE id=?", (uid,)).fetchone()[0] is None
    finally:
        conn.close()


# ── the purge ──────────────────────────────────────────────────────────────

def test_the_purge_leaves_nothing_of_the_user_in_any_table(stack):  # noqa: F811
    db = stack["db"]
    _register(stack, "gone@example.test")
    _register(stack, "stays@example.test")
    gone, stays = _uid(db, "gone@example.test"), _uid(db, "stays@example.test")
    _seed(db, gone)
    _seed(db, stays)
    conn = db.get_db()
    try:
        now = datetime.now(timezone.utc)
        accountdata.request_deletion(conn, gone, now=now)
        assert accountdata.purge_due(conn, now + timedelta(days=13)) == []    # still inside the grace
        assert accountdata.purge_due(conn, now + timedelta(days=14)) == [gone]
        tables = accountdata.user_tables(conn)
        assert {"jobs", "usage_counters", "user_blocklist", "plan_events", "activity_log"} <= set(tables)
        left = {t: conn.execute("SELECT COUNT(*) FROM %s WHERE user_id=?" % t, (gone,)).fetchone()[0] for t in tables}
        assert not any(left.values()), left
        assert conn.execute("SELECT COUNT(*) FROM users WHERE id=?", (gone,)).fetchone()[0] == 0
        # the spend ledger keeps its rows, anonymised - the global total must not drop
        assert conn.execute("SELECT COUNT(*) FROM llm_usage WHERE user_id IS NULL").fetchone()[0] >= 1
        # nobody else is touched
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=?", (stays,)).fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM users WHERE id=?", (stays,)).fetchone()[0] == 1
    finally:
        conn.close()


def test_admin_is_never_purged_even_if_a_row_says_so():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    migrations.run(c)
    c.execute("INSERT INTO users (id,name,email,password_hash,salt,created_date,role,delete_after) "
              "VALUES (1,'a','a@x','h','s','2026','admin','2026-01-01 00:00:00')")
    c.commit()
    assert accountdata.purge_due(c) == []
