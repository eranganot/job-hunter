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
import html
import secrets

import accountdata
import auth

TEST_EMAIL = "test-user@job-hunter.test"
TEST_NAME = "Test User"


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


_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<meta name="robots" content="noindex"/><title>Test account - Job Hunter</title>
<style>
body{margin:0;background:#0f172a;color:#cbd5e1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;line-height:1.55}
main{max-width:38rem;margin:0 auto;padding:2.5rem 1.25rem}
h1{color:#f8fafc;font-size:1.5rem;margin:0 0 .5rem}
.box{background:#1e293b;border:1px solid #334155;border-radius:.9rem;padding:1.1rem 1.2rem;margin:1.2rem 0}
code{background:#0f172a;border:1px solid #334155;border-radius:.4rem;padding:.15rem .45rem;color:#f8fafc;font-size:1rem}
button{background:#4f46e5;color:#fff;border:0;border-radius:.7rem;padding:.8rem 1.2rem;font-size:1rem;font-weight:600;cursor:pointer}
a{color:#a5b4fc}ol{padding-left:1.2rem}li{margin:.35rem 0}.ok{color:#6ee7b7}
</style></head><body><main>
<h1>Test account</h1>
<p>One account for checking the new-user flow. Resetting it deletes everything it holds and creates it again
as a brand-new user, so onboarding runs from the first step and the first search starts when it ends.</p>
{body}
<form method="POST" action="/admin/test-account/reset"><button type="submit">Reset to a brand-new user</button></form>
<p style="margin-top:1.5rem"><a href="/app">Back to the app</a></p>
</main></body></html>"""


def page_html(st: dict, fresh: dict | None = None) -> str:
    if fresh:
        body = ('<div class="box"><p class="ok"><b>Ready.</b> The account is brand-new.</p>'
                '<p>Email: <code>%s</code></p><p>Password: <code>%s</code></p>'
                '<p>The password is shown once. Reset again to get a new one.</p>'
                '<ol><li>Open a private (incognito) window, so you stay signed in as yourself here.</li>'
                '<li>Go to <code>/login</code> on this site and sign in with the details above.</li>'
                '<li>Onboarding starts. When you finish it, the first search starts by itself.</li></ol></div>'
                % (html.escape(fresh["email"]), html.escape(fresh["password"])))
    elif st.get("exists"):
        body = ('<div class="box"><p>Email: <code>%s</code></p><p>Onboarding finished: <b>%s</b>. Jobs found: <b>%d</b>. '
                'Created: %s.</p><p>The password is not stored in readable form. Reset to get a new one.</p></div>'
                % (html.escape(TEST_EMAIL), "yes" if st.get("onboarding_complete") else "no",
                   int(st.get("jobs") or 0), html.escape(str(st.get("created") or "")[:16])))
    else:
        body = '<div class="box"><p>The test account does not exist yet. Reset creates it.</p></div>'
    return _PAGE.replace("{body}", body)
