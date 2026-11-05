"""accountdata.py - a user's data: export it, and delete it (Step 6).

Export: one zip - data.json (every row the app holds about the user, from
every table with a user_id column, secrets removed) and the CV file.

Delete: a request schedules deletion GRACE_DAYS ahead and signs the user out
everywhere; signing in before then cancels it (auth.create_session). The
hourly sweep then purges the account: every row in every table that has a
user_id column - found from the schema at run time, so a table added later is
covered without anyone remembering this file - then the users row and the CV
cache file. Two deliberate exceptions:
  * llm_usage keeps its rows with user_id cleared. It is the ledger the daily
    AI spend ceiling reads (m0007): deleting rows would lower today's global
    total, the one number a ceiling must not lose.
  * plan_events.by_user_id (the admin who made a change) is left as is; it
    names the admin, not the deleted user, whose own rows go by user_id.

Not exported: jobs with status 'locked' - matches the user's plan holds back
(Phase 5). They are the app's output, not the user's data, and exporting them
would hand over exactly what the blur withholds; data.json says how many.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone

import dbdriver

GRACE_DAYS = 14
_FMT = "%Y-%m-%d %H:%M:%S"

# Never exported: credentials and session material.
_SECRET = {
    "users": {"password_hash", "salt", "google_sub"},
    "user_profiles": {"telegram_token", "twilio_auth_token", "twilio_account_sid", "email_smtp_pass"},
}
_SKIP_TABLES = {"sessions", "push_subscriptions", "plan_notices", "schema_migrations"}
_KEEP_ANONYMISED = {"llm_usage"}


def _now():
    return datetime.now(timezone.utc)


def _stamp(dt):
    return dt.astimezone(timezone.utc).strftime(_FMT)


def user_tables(conn) -> list:
    """Every table with a user_id column, on either engine."""
    if dbdriver.dialect_of(conn) == dbdriver.POSTGRES:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND column_name='user_id' ORDER BY table_name").fetchall()
        return [r[0] for r in rows]
    out = []
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        if any(r[1] == "user_id" for r in conn.execute("PRAGMA table_info(%s)" % t).fetchall()):
            out.append(t)
    return out


def _rows(conn, sql, args):
    cur = conn.execute(sql, args)
    return [dict(r) for r in cur.fetchall()]


def _clean(table, row):
    for k in _SECRET.get(table, ()):
        row.pop(k, None)
    for k, v in list(row.items()):
        if isinstance(v, (bytes, bytearray, memoryview)):
            row[k] = "<%d bytes - see the file in this zip>" % len(bytes(v))
    return row


def export_data(conn, user_id: int) -> dict:
    user = _rows(conn, "SELECT * FROM users WHERE id=?", (user_id,))
    out = {"exported_at": _stamp(_now()), "user": _clean("users", user[0]) if user else None, "tables": {}}
    for t in user_tables(conn):
        if t in _SKIP_TABLES:
            continue
        sql = "SELECT * FROM %s WHERE user_id=?" % t
        if t == "jobs":
            sql += " AND COALESCE(status,'') != 'locked'"
        out["tables"][t] = [_clean(t, r) for r in _rows(conn, sql, (user_id,))]
    locked = conn.execute("SELECT COUNT(*) FROM jobs WHERE user_id=? AND status='locked'", (user_id,)).fetchone()[0]
    out["held_back_matches"] = {"count": int(locked or 0),
                                "note": "Matches your plan holds back are not exported; upgrading shows them."}
    return out


def export_zip(conn, user_id: int, cv_bytes: bytes | None = None, cv_name: str = "cv.pdf") -> bytes:
    data = export_data(conn, user_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("data.json", json.dumps(data, indent=2, default=str, ensure_ascii=False))
        if cv_bytes:
            z.writestr(cv_name or "cv.pdf", cv_bytes)
        z.writestr("README.txt",
                   "Your Job Hunter data, exported %s UTC.\n\ndata.json - your account, profile, jobs, "
                   "activity and settings (passwords and access tokens are never exported).\n%s"
                   % (data["exported_at"], "Your CV is the other file in this zip.\n" if cv_bytes else ""))
    return buf.getvalue()


# ── deletion ───────────────────────────────────────────────────────────────

def request_deletion(conn, user_id: int, now=None) -> str:
    """Schedule deletion GRACE_DAYS ahead and end every session. Returns the
    UTC stamp it will happen at."""
    when = _stamp((now or _now()) + timedelta(days=GRACE_DAYS))
    conn.execute("UPDATE users SET delete_after=? WHERE id=?", (when, user_id))
    conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    conn.commit()
    return when


def cancel_deletion(conn, user_id: int) -> bool:
    """True if a pending deletion was cancelled (called on every sign-in)."""
    cur = conn.execute("UPDATE users SET delete_after=NULL WHERE id=? AND delete_after IS NOT NULL", (user_id,))
    conn.commit()
    return bool(cur.rowcount and cur.rowcount > 0)


def purge_user(conn, user_id: int) -> dict:
    """Remove everything the app holds about one user. Returns rows per table."""
    removed = {}
    for t in user_tables(conn):
        if t in _KEEP_ANONYMISED:
            n = conn.execute("UPDATE %s SET user_id=NULL WHERE user_id=?" % t, (user_id,)).rowcount
        else:
            n = conn.execute("DELETE FROM %s WHERE user_id=?" % t, (user_id,)).rowcount
        removed[t] = max(n or 0, 0)
    removed["users"] = max(conn.execute("DELETE FROM users WHERE id=?", (user_id,)).rowcount or 0, 0)
    conn.commit()
    return removed


def purge_due(conn, now=None) -> list:
    """Purge every account whose grace period is over. Returns the user ids."""
    due = [r[0] for r in conn.execute(
        "SELECT id FROM users WHERE delete_after IS NOT NULL AND delete_after <= ? "
        "AND LOWER(COALESCE(role,'user')) != 'admin'", (_stamp(now or _now()),)).fetchall()]
    for uid in due:
        purge_user(conn, uid)
    return due
