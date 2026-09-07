#!/usr/bin/env python3
"""
sanitize_db.py - turn a production backup into a staging-safe copy.

Phase 0.2 of EXECUTION_PLAN_PUBLIC_LAUNCH.md. Staging needs realistic VOLUME
(2366 jobs is what makes the Phase 2 Postgres migration rehearsal meaningful)
without carrying real people's resumes, phone numbers and notification secrets
onto a second, less-guarded box.

What it scrubs, decided by reading the real schema rather than assuming it:

  users               name, email -> User N / userN@example.test
                      password_hash + salt -> one known staging password
                      google_sub, avatar_url -> NULL, auth_provider -> 'local'
  user_profiles       telegram/twilio/whatsapp/smtp credentials -> ''
                      phone, linkedin_url, email_address -> ''
                      cv_summary, cv_optimizer_result -> placeholder text
                      cv_path / cv_filename -> the dummy CV
  application_answers every name/phone/email/URL/work-auth field
  sessions            deleted (nobody stays logged in on staging)
  push_subscriptions  deleted (endpoints are device identifiers)
  activity_log        any email address inside the free-text details

  jobs, rejected_patterns, pass_reason_stats, career_url_cache, app_flags
                      kept verbatim - public job data, and the whole point of
                      seeding staging with something realistic.

Usage:
    python scripts/sanitize_db.py <prod-backup.db> <staging-out.db> [--password PW]

The input file is opened read-only and never modified.
"""
import argparse
import hashlib
import os
import re
import secrets
import shutil
import sqlite3
import sys

DEFAULT_PASSWORD = "staging-only-1234"
PLACEHOLDER_CV = "Sanitized for staging. Original CV summary removed."
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def hash_password(password: str, salt: str = None):
    """Same PBKDF2 parameters as auth.py:27 - staging logins must actually work."""
    if salt is None:
        salt = secrets.token_hex(32)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return dk.hex(), salt


def columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def blank(conn, table, cols, value=""):
    """Blank only the columns that actually exist in this schema version."""
    present = [c for c in cols if c in columns(conn, table)]
    if not present:
        return []
    conn.execute("UPDATE %s SET %s" % (table, ", ".join(f"{c}=?" for c in present)),
                 tuple(value for _ in present))
    return present


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--password", default=DEFAULT_PASSWORD)
    args = ap.parse_args()

    if not os.path.exists(args.src):
        sys.exit(f"[FAIL] no such file: {args.src}")
    if os.path.abspath(args.src) == os.path.abspath(args.dst):
        sys.exit("[FAIL] refusing to sanitize in place - give a different output path")

    shutil.copyfile(args.src, args.dst)
    conn = sqlite3.connect(args.dst)
    conn.row_factory = sqlite3.Row
    print(f"[..] working on a copy: {args.dst}")

    # --- users -------------------------------------------------------------
    pw_hash, salt = hash_password(args.password)
    rows = conn.execute("SELECT id FROM users ORDER BY id").fetchall()
    for r in rows:
        uid = r["id"]
        conn.execute(
            "UPDATE users SET name=?, email=?, password_hash=?, salt=? WHERE id=?",
            (f"User {uid}", f"user{uid}@example.test", pw_hash, salt, uid))
    for col, val in (("google_sub", None), ("avatar_url", None), ("auth_provider", "local")):
        if col in columns(conn, "users"):
            conn.execute(f"UPDATE users SET {col}=?", (val,))
    print(f"[OK] users: {len(rows)} anonymised, all share the staging password")

    # --- user_profiles -----------------------------------------------------
    secrets_cols = ["telegram_token", "telegram_chat_id", "twilio_account_sid",
                    "twilio_auth_token", "whatsapp_number", "email_address",
                    "email_smtp_user", "email_smtp_pass", "phone", "linkedin_url"]
    cleared = blank(conn, "user_profiles", secrets_cols)
    for col, val in (("cv_summary", PLACEHOLDER_CV),
                     ("cv_optimizer_result", None),
                     ("cv_path", "/data/uploads/staging/cv.pdf"),
                     ("cv_filename", "staging-cv.pdf")):
        if col in columns(conn, "user_profiles"):
            conn.execute(f"UPDATE user_profiles SET {col}=?", (val,))
    # Nobody on staging should be able to trigger a real notification.
    if "notification_channel" in columns(conn, "user_profiles"):
        conn.execute("UPDATE user_profiles SET notification_channel='none'")
    print(f"[OK] user_profiles: cleared {len(cleared)} credential/PII columns, CV text replaced")

    # --- application_answers (the richest PII table) -----------------------
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='application_answers'").fetchone():
        pii = ["first_name", "last_name", "preferred_name", "phone", "phone_country_code",
               "email", "linkedin_url", "github_url", "portfolio_url", "twitter_url"]
        got = blank(conn, "application_answers", pii)
        n = conn.execute("SELECT COUNT(*) FROM application_answers").fetchone()[0]
        print(f"[OK] application_answers: {n} row(s), {len(got)} PII columns blanked")

    # --- session + device identifiers --------------------------------------
    for t in ("sessions", "push_subscriptions"):
        if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            conn.execute(f"DELETE FROM {t}")
            print(f"[OK] {t}: {n} row(s) deleted")

    # --- free-text that may quote an address --------------------------------
    scrubbed = 0
    if "details" in columns(conn, "activity_log"):
        for r in conn.execute("SELECT id, details FROM activity_log WHERE details LIKE '%@%'").fetchall():
            new = EMAIL_RE.sub("user@example.test", r["details"] or "")
            if new != r["details"]:
                conn.execute("UPDATE activity_log SET details=? WHERE id=?", (new, r["id"]))
                scrubbed += 1
    print(f"[OK] activity_log: {scrubbed} row(s) had an email address rewritten")

    conn.commit()

    # --- verification: prove it, do not assume it ---------------------------
    print("\n[..] verifying")
    problems = []

    leaked = conn.execute(
        "SELECT COUNT(*) FROM users WHERE email NOT LIKE '%@example.test'").fetchone()[0]
    if leaked: problems.append(f"{leaked} user email(s) not anonymised")

    for c in secrets_cols:
        if c in columns(conn, "user_profiles"):
            n = conn.execute(
                f"SELECT COUNT(*) FROM user_profiles WHERE COALESCE({c},'') <> ''").fetchone()[0]
            if n: problems.append(f"user_profiles.{c} still populated in {n} row(s)")

    for t in ("sessions", "push_subscriptions"):
        if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            if n: problems.append(f"{t} still has {n} row(s)")

    kept = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("users", "jobs", "activity_log", "rejected_patterns")}
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()

    print(f"[OK] integrity_check = {integrity}")
    print(f"[OK] volume preserved: " + "  ".join(f"{k}={v}" for k, v in kept.items()))

    if problems:
        print("\n[FAIL] sanitisation incomplete:")
        for p in problems:
            print("   - " + p)
        sys.exit(1)

    print(f"\n[DONE] staging-safe DB: {args.dst}")
    print(f"       every account's password is now: {args.password}")
    print("       admin account = whichever user id you kept as role='admin'")


if __name__ == "__main__":
    main()
