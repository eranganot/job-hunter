"""testaccount.py - one resettable test account, for checking the new-user flow.

The owner asked for "a fake user so I could check (and re-check)" onboarding
(2026-11-11). Onboarding runs once per account, and the first search starts
only the first time setup ends, so re-checking needs a genuinely new account
each time. This module owns one fixed account and can put it back to
brand-new: everything it holds is purged (the same purge account deletion
uses) and the account is created again with a fresh password.

- The address is on the reserved `.test` domain: it can never receive mail,
  and app.send_email refuses to send to @job-hunter.test.
- It is created here, not through /register, so it has no `signup` funnel
  event and never appears in the Admin signup funnel (a cohort of signups).
- Its email is marked confirmed, since nobody can click a link sent to it.
"""
import secrets

import accountdata
import auth

TEST_EMAIL = "test-user@job-hunter.test"
TEST_NAME = "Test User"


def is_test_address(addr) -> bool:
    return str(addr or "").strip().lower() == TEST_EMAIL


def status(conn) -> dict:
    r = conn.execute("SELECT id, created_date FROM users WHERE email=?", (TEST_EMAIL,)).fetchone()
    if not r:
        return {"exists": False}
    p = conn.execute("SELECT onboarding_complete FROM user_profiles WHERE user_id=?", (r["id"],)).fetchone()
    jobs = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE user_id=?", (r["id"],)).fetchone()["n"]
    return {"exists": True, "id": r["id"], "created": r["created_date"],
            "onboarding_complete": bool(p and p["onboarding_complete"]), "jobs": int(jobs)}


def reset(get_db) -> dict:
    """Purge the test account if it exists and create it again, brand-new.
    Returns {"email", "password", "removed": rows per table}."""
    removed = {}
    conn = get_db()
    try:
        r = conn.execute("SELECT id, role FROM users WHERE email=?", (TEST_EMAIL,)).fetchone()
        if r:
            if str(r["role"] or "").lower() == "admin":
                raise RuntimeError("the test address belongs to an admin account - refusing to purge it")
            removed = accountdata.purge_user(conn, r["id"])
    finally:
        conn.close()
    password = secrets.token_urlsafe(9)
    uid, err = auth.create_user(TEST_NAME, TEST_EMAIL, password)
    if err:
        raise RuntimeError("could not create the test account: %s" % err)
    auth.mark_email_verified(uid)
    return {"email": TEST_EMAIL, "password": password, "removed": removed, "id": uid}
