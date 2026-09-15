"""
tests/test_routes.py — route-level + tenant-isolation harness.

Boots the real `app.Handler` on an ephemeral port against a throwaway SQLite DB
and drives it over real HTTP. This is the safety net every later phase of
EXECUTION_PLAN_PUBLIC_LAUNCH.md is verified against: before this file, the
7,500-line HTTP handler had zero tests and nothing proved that user B cannot
touch user A's data.

Nothing here talks to Gemini, Resend, Playwright or the network.
"""
import base64
import http.client
import itertools
import json
import os
import pathlib
import shutil
import tempfile
import threading
import time
import urllib.parse
from http.server import HTTPServer
from socketserver import ThreadingMixIn

import pytest


# ── Harness ───────────────────────────────────────────────────────────────────

class _Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# Transport-level errors that mean "the socket died", not "the app answered
# something wrong". One full-suite run on Windows/py3.12 showed a single
# unexplained failure in this file that never reproduced (7/7 in isolation,
# 220/220 on Linux). Rather than invent a cause, the client retries ONCE on a
# dead socket and never on an HTTP status - so a real behavioural regression
# still fails, while a dropped connection does not masquerade as one.
_TRANSPORT_ERRORS = (ConnectionResetError, ConnectionAbortedError,
                     BrokenPipeError, http.client.RemoteDisconnected,
                     http.client.BadStatusLine)


class Client:
    """Minimal HTTP client with cookie memory, so a session survives requests."""

    def __init__(self, port):
        self.port = port
        self.cookie = None

    def _with_retry(self, fn):
        try:
            return fn()
        except _TRANSPORT_ERRORS:
            time.sleep(0.05)
            return fn()

    def _headers(self, extra=None):
        h = dict(extra or {})
        if self.cookie:
            h["Cookie"] = self.cookie
        return h

    def _capture_cookie(self, resp):
        raw = resp.getheader("Set-Cookie")
        if raw:
            self.cookie = raw.split(";")[0]

    def get(self, path, extra_headers=None):
        return self._with_retry(lambda: self._get(path, extra_headers))

    def _get(self, path, extra_headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path, headers=self._headers(extra_headers))
        resp = conn.getresponse()
        body = resp.read()
        self._capture_cookie(resp)
        conn.close()
        return resp.status, resp.getheader("Location"), body

    def post_json(self, path, payload, extra_headers=None):
        return self._with_retry(lambda: self._post_json(path, payload, extra_headers))

    def _post_json(self, path, payload, extra_headers=None):
        body = json.dumps(payload).encode()
        hdrs = {"Content-Type": "application/json", "Content-Length": str(len(body))}
        hdrs.update(extra_headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", path, body=body, headers=self._headers(hdrs))
        resp = conn.getresponse()
        out = resp.read()
        self._capture_cookie(resp)
        conn.close()
        return resp.status, resp.getheader("Location"), out

    def post_form(self, path, fields):
        return self._with_retry(lambda: self._post_form(path, fields))

    def _post_form(self, path, fields):
        body = urllib.parse.urlencode(fields).encode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", path, body=body,
                     headers=self._headers({
                         "Content-Type": "application/x-www-form-urlencoded",
                         "Content-Length": str(len(body))}))
        resp = conn.getresponse()
        out = resp.read()
        self._capture_cookie(resp)
        conn.close()
        return resp.status, resp.getheader("Location"), out


@pytest.fixture(scope="module")
def stack():
    """Import app.py against a temp DB, serve it, hand back (client-factory, db)."""
    tmp = tempfile.mkdtemp(prefix="jh-routes-")
    os.environ["DATABASE_PATH"] = os.path.join(tmp, "test.db")
    os.environ["UPLOADS_DIR"] = os.path.join(tmp, "uploads")
    os.environ["ADMIN_EMAIL"] = "admin@example.test"
    os.environ.setdefault("GEMINI_API_KEY", "")
    os.environ.setdefault("RESEND_API_KEY", "")
    os.environ.pop("APPLY_ENGINE_ENABLED", None)   # apply engine stays off in tests

    import app as app_module          # noqa: E402  (import after env is set)
    import auth                       # noqa: E402
    import db as database             # noqa: E402

    # app.py wires the DB path and admin email at IMPORT time (app.py:160-162).
    # Python caches modules, so if another test file imported app before this
    # fixture ran, that import already fixed both values from a different env and
    # our `import app` above is a no-op. Re-wire explicitly so this module is
    # independent of test ordering — otherwise these tests would read whatever DB
    # the first importer chose, up to and including the repo's real jobs.db.
    database.set_db_path(os.environ["DATABASE_PATH"])
    auth.set_db_getter(database.get_db)
    auth.set_admin_email(os.environ["ADMIN_EMAIL"])
    app_module.UPLOADS_DIR = os.environ["UPLOADS_DIR"]
    # storage.py holds its own copy, set by app.py at import time. Re-wire it for
    # the same reason the lines above exist: if another test module imported app
    # first, that import fixed the value from a different env, and without this
    # these tests would read and write the CV cache of whatever directory that
    # importer chose.
    import storage                    # noqa: E402
    storage.set_uploads_dir(os.environ["UPLOADS_DIR"])
    # Same trap, one constant further along. app.py reads ADMIN_EMAIL at import
    # time too, so if another test module imported app first this is frozen at
    # "" - and every route gated on `user["email"] != ADMIN_EMAIL` then returns
    # 403 to everybody, including the admin. Any authorisation test against such
    # a route would pass without proving anything. Found 2026-09-14 when an
    # isolation test expected 404 and got 403.
    app_module.ADMIN_EMAIL = os.environ["ADMIN_EMAIL"]
    os.makedirs(os.environ["UPLOADS_DIR"], exist_ok=True)

    database.init_db()

    # Registration notifies the admin by email; keep the suite offline.
    #
    # ASSIGNED TO THE MODULE, SO IT MUST BE RESTORED. Python caches modules, so
    # a stub written here stays installed for the rest of the process - every
    # later test file that called app.deliver_notification got a no-op and saw
    # nothing delivered. It went unnoticed for as long as nothing ran this file
    # before tests/test_notifications.py; borrowing `stack` from two new
    # modules created that order and broke four tests that had never touched
    # this fixture. Proven 2026-09-15 by running the three files together.
    _saved = {name: getattr(app_module, name)
              for name in ("notify_admin_new_user", "deliver_notification")}
    app_module.notify_admin_new_user = lambda **_kw: None
    app_module.deliver_notification = lambda *_a, **_kw: None

    srv = _Server(("127.0.0.1", 0), app_module.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    yield {"port": port, "db": database, "app": app_module}

    for name, fn in _saved.items():
        setattr(app_module, name, fn)
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="module")
def users(stack):
    """Two registered users (A and B) plus the admin, each with a live session.

    Other test modules import this fixture, so it runs once PER MODULE in the
    same process, from the same 127.0.0.1. The register limiter is per-IP and
    process-global, so the second module to ask got 429 on the third signup and
    every test in it errored at setup - a fixture defeated by its own success.
    Cleared here rather than in each borrowing module: the fixture's contract is
    "three registered users", and a limiter another module tripped is not the
    borrower's problem to know about.
    """
    import ratelimit
    ratelimit.reset_all()
    made = {}
    for key, email in (("a", "alice@example.test"),
                       ("b", "bob@example.test"),
                       ("admin", "admin@example.test")):
        c = Client(stack["port"])
        status, location, _ = c.post_form("/register", {
            "name": key, "email": email,
            "password": "correct-horse-1", "password2": "correct-horse-1"})
        assert status == 302, f"register {email} returned {status}"
        # /app, not /onboarding: the setup flow moved into the PWA (2026-09-15)
        # and decides whether to show itself from the onboarding flags on
        # /api/me. Sending new accounts to the legacy page was the last route by
        # which a brand-new user met the old design before anything else.
        assert location == "/app", location
        assert c.cookie, f"no session cookie for {email}"
        made[key] = c
    return made


_JOB_SEQ = itertools.count(1)


def _new_job(database, user_id, title="VP of Product", company="Acme"):
    # jobs has UNIQUE(user_id, url) — every fixture job needs its own URL.
    url = f"https://example.test/job/{next(_JOB_SEQ)}"
    conn = database.get_db()
    conn.execute(
        "INSERT INTO jobs (user_id, title, company, location, url, status, match_score) "
        "VALUES (?,?,?,?,?,?,?)",
        (user_id, title, company, "Tel Aviv", url, "new", 90))
    job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    return job_id


def _job_row(database, job_id):
    conn = database.get_db()
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return row


def _user_id(database, email):
    conn = database.get_db()
    row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    return row["id"]


# ── Public surface ────────────────────────────────────────────────────────────

def test_public_pages_render(stack):
    c = Client(stack["port"])
    for path in ("/login", "/register"):
        status, _, body = c.get(path)
        assert status == 200, f"{path} returned {status}"
        assert b"<!DOCTYPE html>" in body


def test_health_is_public_and_reports_shape(stack):
    status, _, body = Client(stack["port"]).get("/api/health")
    assert status == 200
    payload = json.loads(body)
    for key in ("status", "active_users", "total_jobs", "scheduler", "schema_version"):
        assert key in payload
    # Phase 2a: health must report the applied schema version so a deploy can be
    # verified from outside. 0 would mean the migrations never ran on that box.
    import migrations as _m
    assert payload["schema_version"] == max(v for v, _n, _f in _m.MIGRATIONS)


# ── Authentication gates ──────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/dashboard", "/settings", "/onboarding", "/admin"])
def test_pages_require_login(stack, path):
    status, location, _ = Client(stack["port"]).get(path)
    assert status == 302, f"{path} let an anonymous visitor in ({status})"
    assert location == "/login"


@pytest.mark.parametrize("path", ["/api/me", "/api/jobs", "/api/stats", "/api/activity",
                                  "/api/patterns", "/api/learned"])
# /api/blocklist is POST-only (GET is an unrouted 404) — covered below.
def test_get_apis_require_login(stack, path):
    status, location, _ = Client(stack["port"]).get(path)
    assert status == 302, f"{path} served data anonymously ({status})"
    assert location == "/login"


@pytest.mark.parametrize("path,payload", [
    ("/api/save-profile", {"job_titles": ["x"]}),
    ("/api/save-schedule", {"schedule_frequency": "daily"}),
    ("/api/save-notifications", {"notification_channel": "none"}),
    ("/api/blocklist", {"company": "Acme"}),
    ("/api/jobs/bulk", {"action": "approve", "ids": [1]}),
    ("/api/set-stage", {"id": 1, "stage": "screening"}),
    ("/api/jobs/1/approve", {}),
    # The three /app could not reach until 2026-09-15. They are now callable
    # from the UI, so their gates are worth asserting rather than assuming.
    ("/api/change-password", {"current_password": "x", "new_password": "y"}),
    ("/api/analyze-cv", {}),
    ("/api/test-notification", {"channel": "telegram"}),
])
def test_post_apis_require_login(stack, path, payload):
    status, _, _ = Client(stack["port"]).post_json(path, payload)
    assert status == 401, f"{path} accepted an anonymous POST ({status})"


def test_admin_api_never_serves_data_anonymously(stack):
    status, _, body = Client(stack["port"]).get("/api/admin/users")
    assert status != 200, "admin user list served anonymously"
    assert b"@example.test" not in body


# ── Logged-in happy path ──────────────────────────────────────────────────────

def test_session_identifies_the_right_user(users):
    status, _, body = users["a"].get("/api/me")
    assert status == 200
    assert json.loads(body)["email"] == "alice@example.test"


def test_dashboard_and_app_shell_render_for_a_member(users):
    status, _, body = users["a"].get("/dashboard")
    assert status in (200, 302)          # 302 while onboarding is incomplete
    status, _, body = users["a"].get("/app")
    assert status == 200, "the PWA shell did not serve"
    assert b"<!doctype html>" in body.lower()


def test_non_admin_is_kept_out_of_admin(users):
    # The property is "bounced away from /admin, to wherever home is" - not a
    # literal path. Pinning "/dashboard" made this fail when the home
    # destination flipped to /app (Phase 4 item 6), which is a test asserting a
    # decision rather than a behaviour.
    import app as _app
    status, location, _ = users["b"].get("/admin")
    assert status == 302 and location == _app.home_url()
    status, _, body = users["b"].get("/api/admin/users")
    assert status == 403, f"non-admin reached the admin user list ({status})"


def test_admin_account_gets_the_admin_role(users):
    status, _, body = users["admin"].get("/api/me")
    assert status == 200
    assert json.loads(body)["role"] == "admin"


# ── Tenant isolation: the tests that matter before strangers sign up ──────────

def test_job_list_is_scoped_to_its_owner(stack, users):
    db = stack["db"]
    _new_job(db, _user_id(db, "alice@example.test"), title="Alice Only")
    status, _, body = users["b"].get("/api/jobs?status=new")
    assert status == 200
    assert b"Alice Only" not in body, "user B can see user A's jobs"


@pytest.mark.parametrize("action", ["approve", "reject", "later", "applied", "restore"])
def test_foreign_user_cannot_mutate_a_job(stack, users, action):
    db = stack["db"]
    job_id = _new_job(db, _user_id(db, "alice@example.test"))
    before = _job_row(db, job_id)["status"]

    status, _, _ = users["b"].post_json(f"/api/jobs/{job_id}/{action}", {"reason": "nope"})
    assert status == 404, f"/{action} on a foreign job returned {status}, expected 404"
    assert _job_row(db, job_id)["status"] == before, f"user B changed A's job via /{action}"


def test_foreign_user_cannot_bulk_mutate(stack, users):
    db = stack["db"]
    job_id = _new_job(db, _user_id(db, "alice@example.test"))
    before = _job_row(db, job_id)["status"]

    status, _, body = users["b"].post_json("/api/jobs/bulk",
                                           {"action": "approve", "ids": [job_id]})
    assert status == 200
    assert json.loads(body)["updated"] == 0, "bulk endpoint counted a foreign job"
    assert _job_row(db, job_id)["status"] == before, "user B bulk-changed A's job"


def test_foreign_user_cannot_set_stage(stack, users):
    db = stack["db"]
    job_id = _new_job(db, _user_id(db, "alice@example.test"))
    before = _job_row(db, job_id)["apply_status"]

    users["b"].post_json("/api/set-stage", {"id": job_id, "stage": "interviewing"})
    assert _job_row(db, job_id)["apply_status"] == before, "user B set a stage on A's job"


def test_foreign_user_cannot_trigger_apply(stack, users):
    db = stack["db"]
    job_id = _new_job(db, _user_id(db, "alice@example.test"))

    status, _, _ = users["b"].post_json(f"/api/jobs/{job_id}/apply-now", {})
    assert status == 404, f"apply-now on a foreign job returned {status}"
    assert _job_row(db, job_id)["apply_status"] in (None, ""), "apply was triggered on A's job"


def test_logout_ends_the_session(stack, users):
    c = Client(stack["port"])
    c.post_form("/register", {"name": "temp", "email": "temp@example.test",
                              "password": "correct-horse-1", "password2": "correct-horse-1"})
    assert c.get("/api/me")[0] == 200
    c.get("/logout")
    status, location, _ = c.get("/api/me")
    assert status == 302 and location == "/login", "session survived logout"


# ── Phase 2d: CV bytes live in the database, the volume is a cache ────────────

# Not valid UTF-8, contains NULs, ends with one: anything that mangled it on the
# way through would show up as a length or byte mismatch rather than as silence.
_PDF = b"%PDF-1.7\n" + bytes(range(256)) + b"\x00trailer\n%%EOF\x00"


def _upload_cv(client, data=_PDF, filename="resume.pdf"):
    return client.post_json("/api/upload-cv", {
        "filename": filename,
        "data": base64.b64encode(data).decode(),
    })


def test_cv_upload_then_download_returns_the_same_bytes(stack, users):
    status, _loc, body = _upload_cv(users["a"])
    assert status == 200, body
    assert json.loads(body).get("success") is True

    status, _loc, got = users["a"].get("/api/cv")
    assert status == 200
    assert got == _PDF, "the CV came back different from the one uploaded"


def test_the_cv_survives_losing_the_uploads_volume(stack, users):
    """
    The whole point of Phase 2d. A redeploy without a volume, a fresh container,
    a restored backup: the files are gone and the CV must still be served.
    """
    _upload_cv(users["b"])
    shutil.rmtree(os.environ["UPLOADS_DIR"])          # the volume is gone
    os.makedirs(os.environ["UPLOADS_DIR"], exist_ok=True)

    status, _loc, got = users["b"].get("/api/cv")
    assert status == 200, "a cold volume made the CV unreachable"
    assert got == _PDF


def test_a_user_cannot_download_another_users_cv(stack, users):
    """The bytes moved into a shared table; tenant isolation has to move with them."""
    _upload_cv(users["a"], b"%PDF-alice-only\x00")
    _upload_cv(users["b"], b"%PDF-bob-only\x00")

    _st, _l, alice = users["a"].get("/api/cv")
    _st, _l, bob = users["b"].get("/api/cv")
    assert alice == b"%PDF-alice-only\x00"
    assert bob == b"%PDF-bob-only\x00"


def test_no_cv_is_a_404_not_a_500(stack, users):
    status, _loc, _body = users["admin"].get("/api/cv")
    assert status == 404


def test_a_non_pdf_upload_is_refused(stack, users):
    status, _loc, body = _upload_cv(users["a"], b"MZ\x90not a pdf", "payload.exe")
    assert status == 200
    assert "error" in json.loads(body)


# ── Phase 3: state-changing requests made from another site ──────────────────
#
# The session cookie is already SameSite=Lax, so a cross-site POST never carries
# it. What Lax does not cover is a top-level GET navigation - and an audit on
# 2026-09-14 found two admin GETs that change state: /api/admin/dedup deletes
# rows, and /api/admin/apply-test?mode=live submits an application and bypasses
# the kill switch. A link clicked while signed in as admin was enough.

CROSS_SITE = {"Sec-Fetch-Site": "cross-site"}
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}


def test_a_cross_site_get_cannot_delete_jobs(stack, users):
    status, _loc, body = users["admin"].get("/api/admin/dedup", CROSS_SITE)
    assert status == 403, "a cross-site GET reached the row-deleting route"
    assert b"Cross-site" in body


def test_a_cross_site_get_cannot_submit_an_application(stack, users):
    status, _loc, _b = users["admin"].get(
        "/api/admin/apply-test?job_id=1&mode=live", CROSS_SITE)
    assert status == 403


def test_the_dry_run_is_still_reachable_cross_site(stack, users):
    """Only the half that changes state is refused; the diagnostic still works."""
    status, _loc, _b = users["admin"].get("/api/admin/apply-test", CROSS_SITE)
    assert status != 403


def test_a_cross_site_post_is_refused(stack, users):
    status, _loc, _b = users["a"].post_json("/api/save-notifications", {}, CROSS_SITE)
    assert status == 403


def test_same_origin_requests_are_unaffected(stack, users):
    """The gate must not break the app it protects."""
    status, _loc, _b = users["admin"].get("/api/admin/dedup", SAME_ORIGIN)
    assert status == 200, "the admin panel's own call was refused"


def test_requests_without_the_header_still_work(stack, users):
    """
    curl, scripts and Eran testing by hand send no Sec-Fetch-Site. They cannot
    be CSRF - that needs a browser holding someone else's cookie - so they pass.
    """
    status, _loc, _b = users["admin"].get("/api/admin/dedup")
    assert status == 200


def test_the_unreachable_delete_is_gone():
    """Dead code holding a DELETE is a landmine: removing one `return` re-arms it."""
    import io as _io
    src = _io.open("app.py", encoding="utf-8").read()
    assert "DELETE FROM jobs WHERE user_id=? AND url LIKE" not in src


# ── Phase 3: credentials encrypted at rest, end to end over HTTP ─────────────

_BOT_TOKEN = "987654321:AAF-a-real-shaped-telegram-token"


def _stored_token(database, email):
    conn = database.get_db()
    row = conn.execute(
        "SELECT p.telegram_token FROM user_profiles p JOIN users u ON u.id=p.user_id "
        "WHERE u.email=?", (email,)).fetchone()
    conn.close()
    return row["telegram_token"] if row else None


def test_a_saved_credential_is_ciphertext_in_the_database(stack, users, monkeypatch):
    monkeypatch.setenv("JH_ENCRYPTION_KEY", "a long random passphrase for tests")

    status, _l, _b = users["a"].post_json("/api/save-notifications", {
        "notification_channel": "telegram",
        "telegram_token": _BOT_TOKEN,
        "telegram_chat_id": "555",
    })
    assert status == 200

    stored = _stored_token(stack["db"], "alice@example.test")
    assert stored.startswith("enc:v1:"), "the token was written in the clear"
    assert _BOT_TOKEN not in stored


def test_the_settings_page_still_sees_the_real_value(stack, users, monkeypatch):
    """Encrypted at rest must not mean ciphertext in the user's input box."""
    monkeypatch.setenv("JH_ENCRYPTION_KEY", "a long random passphrase for tests")

    users["a"].post_json("/api/save-notifications",
                         {"telegram_token": _BOT_TOKEN, "telegram_chat_id": "555"})
    status, _l, body = users["a"].get("/api/me")
    assert status == 200
    me = json.loads(body)
    assert me["telegram_token"] == _BOT_TOKEN
    assert me["telegram_chat_id"] == "555", "a non-secret field was mangled"


def test_without_a_key_the_behaviour_is_exactly_what_it_was(stack, users, monkeypatch):
    """Turning the feature off must be a non-regression, not an error."""
    monkeypatch.delenv("JH_ENCRYPTION_KEY", raising=False)

    users["b"].post_json("/api/save-notifications",
                         {"telegram_token": "plain-token-no-key", "telegram_chat_id": "7"})
    assert _stored_token(stack["db"], "bob@example.test") == "plain-token-no-key"

    status, _l, body = users["b"].get("/api/me")
    assert status == 200 and json.loads(body)["telegram_token"] == "plain-token-no-key"


def test_health_says_whether_credentials_are_encrypted(stack, users, monkeypatch):
    monkeypatch.setenv("JH_ENCRYPTION_KEY", "a long random passphrase for tests")
    _st, _l, body = users["a"].get("/api/health")
    assert json.loads(body)["credentials_encrypted"] is True

    monkeypatch.delenv("JH_ENCRYPTION_KEY", raising=False)
    _st, _l, body = users["a"].get("/api/health")
    assert json.loads(body)["credentials_encrypted"] is False


# ── Phase 3: the two doors that had no limiter ───────────────────────────────

def test_register_is_rate_limited_per_address(stack, users, monkeypatch):
    """A script could create accounts in a loop; nothing counted them."""
    import ratelimit
    ratelimit.reset_all()
    monkeypatch.setenv("JH_RL_REGISTER_MAX", "3")

    c = Client(stack["port"])
    seen = []
    for i in range(5):
        status, _l, _b = c.post_form("/register", {
            "name": "bot%d" % i, "email": "bot%d@example.test" % i,
            "password": "correct-horse-1", "password2": "correct-horse-1"})
        seen.append(status)
    ratelimit.reset_all()

    assert 429 in seen, "register accepted every attempt: %s" % seen
    assert seen.index(429) >= 3, "limited too early: %s" % seen


def test_run_search_is_rate_limited_per_user(stack, users, monkeypatch):
    """
    Each call spawns a thread that spends Gemini quota, and nothing capped it.

    Note the handler's order: the "no AI key configured" 400 comes FIRST, so an
    unconfigured server is never rate-limited - there is nothing to spend. That
    is right, and it is why this test has to configure one. The search itself is
    stubbed: the point is the gate, not the search.
    """
    import ratelimit
    ratelimit.reset_all()
    monkeypatch.setenv("JH_RL_RUN_SEARCH_MAX", "2")
    monkeypatch.setattr(stack["app"], "GEMINI_KEY", "test-key", raising=False)
    monkeypatch.setattr(stack["app"], "run_job_search", lambda *_a, **_k: None, raising=False)

    seen = [users["a"].post_json("/api/run-search", {})[0] for _ in range(4)]
    ratelimit.reset_all()

    assert seen[:2] == [200, 200], "the first calls were not allowed: %s" % seen
    assert 429 in seen, "run-search accepted every attempt: %s" % seen


def test_one_users_limit_does_not_block_another(stack, users, monkeypatch):
    import ratelimit
    ratelimit.reset_all()
    monkeypatch.setenv("JH_RL_RUN_SEARCH_MAX", "1")
    monkeypatch.setattr(stack["app"], "GEMINI_KEY", "test-key", raising=False)
    monkeypatch.setattr(stack["app"], "run_job_search", lambda *_a, **_k: None, raising=False)

    assert users["a"].post_json("/api/run-search", {})[0] == 200
    blocked = users["a"].post_json("/api/run-search", {})[0]
    other = users["b"].post_json("/api/run-search", {})[0]
    ratelimit.reset_all()

    assert blocked == 429
    assert other != 429, "limiting one user locked out another"


def test_separate_addresses_are_separate_buckets():
    """
    Behind Railway the socket peer is the proxy. Keying on it would put every
    user in one bucket - one typo'd password locking out everybody.
    """
    import ratelimit
    ratelimit.reset_all()
    assert ratelimit.check_and_record("register", "203.0.113.1") == 0
    assert ratelimit.check_and_record("register", "203.0.113.2") == 0
    ratelimit.reset_all()


# ── Phase 3: the isolation sweep ─────────────────────────────────────────────
#
# A static pass over app.py found 45 SQL statements touching user-owned tables
# with no user_id in the WHERE clause. Most are background workers acting on ids
# already resolved from a scoped query. The ones that matter are the routes that
# take an id FROM THE CALLER, and of those, three had no test: cover-letter,
# check-status, and patterns/forget. These attack all three.

def test_even_the_admin_cannot_write_a_cover_letter_onto_someone_elses_job(stack, users):
    """
    This route is admin-only, so attacking it as an ordinary user proves nothing
    - the 403 fires before any scoping does. The admin is the only caller that
    gets past the gate, so the admin is who has to be blocked by the scope.
    """
    db = stack["db"]
    job_id = _new_job(db, _user_id(db, "alice@example.test"))

    status, _l, _b = users["admin"].post_json(
        f"/api/jobs/{job_id}/cover-letter", {"action": "save", "letter": "written by admin"})

    row = _job_row(db, job_id)
    letter = row["cover_letter"] if "cover_letter" in row.keys() else None
    assert status == 404, f"admin reached another user's job (status {status})"
    assert letter != "written by admin", "the admin wrote a cover letter onto A's job"


def test_foreign_user_cannot_trigger_a_status_check_on_a_job(stack, users):
    db = stack["db"]
    job_id = _new_job(db, _user_id(db, "alice@example.test"))
    before = _job_row(db, job_id)

    status, _l, _b = users["b"].post_json(f"/api/jobs/{job_id}/check-status", {})

    after = _job_row(db, job_id)
    assert status == 404, f"check-status on a foreign job returned {status}"
    assert after["status"] == before["status"], "user B changed A's job via check-status"


def test_foreign_user_cannot_delete_anothers_rejected_pattern(stack, users):
    db = stack["db"]
    alice = _user_id(db, "alice@example.test")
    conn = db.get_db()
    conn.execute(
        "INSERT INTO rejected_patterns (user_id, company, title, notes) VALUES (?,?,?,?)",
        (alice, "AcmeOnly", "VP Product", "alice's pattern"))
    pid = conn.execute("SELECT id FROM rejected_patterns WHERE company=?",
                       ("AcmeOnly",)).fetchone()[0]
    conn.commit()
    conn.close()

    users["b"].post_json("/api/patterns/forget", {"id": pid})

    conn = db.get_db()
    still = conn.execute("SELECT COUNT(*) FROM rejected_patterns WHERE id=?", (pid,)).fetchone()[0]
    conn.close()
    assert still == 1, "user B deleted A's rejected pattern"


# ── Phase 3: run-search writes a row instead of spawning a thread ────────────

def _clear_queue(database):
    """
    Other tests in this file enqueue runs, and the queue's dedupe is global per
    user - so a test that asserts "this enqueue worked" has to start from a
    known state or it reads someone else's leftover row as its own failure.
    """
    conn = database.get_db()
    conn.execute("DELETE FROM job_runs")
    conn.commit()
    conn.close()


def test_run_search_enqueues_instead_of_starting_a_thread(stack, users, monkeypatch):
    """
    The change users feel: the request returns after a write, not after
    starting a minutes-long thread inside the web process.
    """
    import ratelimit
    ratelimit.reset_all()
    _clear_queue(stack["db"])
    monkeypatch.setattr(stack["app"], "GEMINI_KEY", "test-key", raising=False)

    status, _l, body = users["a"].post_json("/api/run-search", {})
    assert status == 200
    out = json.loads(body)
    assert out["status"] == "queued" and out["run_id"], body

    conn = stack["db"].get_db()
    row = conn.execute("SELECT kind, status FROM job_runs WHERE id=?", (out["run_id"],)).fetchone()
    conn.close()
    ratelimit.reset_all()
    assert row["kind"] == "search" and row["status"] == "queued"


def test_pressing_search_twice_does_not_queue_two_runs(stack, users, monkeypatch):
    """The dedupe, from the user's side: a double-click is not a second search."""
    import ratelimit
    ratelimit.reset_all()
    _clear_queue(stack["db"])
    monkeypatch.setattr(stack["app"], "GEMINI_KEY", "test-key", raising=False)

    first = json.loads(users["b"].post_json("/api/run-search", {})[2])
    second = json.loads(users["b"].post_json("/api/run-search", {})[2])
    ratelimit.reset_all()

    assert first["status"] == "queued" and first["run_id"]
    assert second["status"] == "already_running" and second["run_id"] is None


def test_health_reports_queue_depth(stack, users):
    _st, _l, body = users["a"].get("/api/health")
    q = json.loads(body)["queue"]
    assert "queued" in q and "running" in q, q


# ── Cost guardrails at the route boundary ────────────────────────────────────
#
# jobqueue and gemini own the limits; these tests are about what the USER is
# told when one trips. A cap that answers with a generic 500, or with
# "already_running" for work that is never coming, is a support ticket rather
# than a guardrail.

def test_health_publishes_todays_spend_against_todays_ceilings(stack, users):
    """The number that decides what the ceiling should be has to be readable
    from outside the box, or the ceiling stays the guess it shipped as."""
    status, _loc, body = users["admin"].get("/api/health")
    assert status == 200
    llm = json.loads(body)["llm"]
    assert set(llm) >= {"day", "global", "limits"}
    assert set(llm["limits"]) >= {"global_calls", "user_calls", "enforce"}
    assert set(llm["global"]) == {"calls", "tokens"}


def test_a_capped_search_says_so_and_says_when_it_clears(stack, users, monkeypatch):
    """429 and a reason, not 403 and a shrug: this clears at midnight UTC."""
    import jobqueue
    c = Client(stack["port"])
    c.post_form("/register", {"name": "capped", "email": "capped@example.test",
                              "password": "correct-horse-1", "password2": "correct-horse-1"})
    uid = stack["db"].get_db().execute(
        "SELECT id FROM users WHERE email='capped@example.test'").fetchone()["id"]

    # The route refuses with 400 before any of this when no key is configured.
    monkeypatch.setattr(stack["app"], "GEMINI_KEY", "test-key")

    real = jobqueue.enqueue
    def _capped(user_id, kind, *a, **kw):
        if user_id == uid and kind == "search":
            raise jobqueue.DailyCapReached("search", 20, 20)
        return real(user_id, kind, *a, **kw)
    monkeypatch.setattr(jobqueue, "enqueue", _capped)

    status, _loc, body = c.post_json("/api/run-search", {},
                                     {"Sec-Fetch-Site": "same-origin"})
    assert status == 429
    payload = json.loads(body)
    assert payload["status"] == "daily_cap"
    assert "20 of 20" in payload["error"] and "midnight" in payload["error"].lower()
    # The thing a capped user must NOT be told is that work is on its way.
    assert "already_running" not in body.decode()


def test_manual_apply_is_queued_rather_than_spawned(stack, users, monkeypatch):
    """The last raw thread in the app. A spawned apply skipped both the
    one-run-per-user rule and the daily cap, which is the hole the cap exists
    to close - so the route is asserted to go through the queue, not to start
    a thread."""
    import jobqueue
    seen = []
    real = jobqueue.enqueue
    monkeypatch.setattr(jobqueue, "enqueue",
                        lambda uid, kind, *a, **kw: (seen.append((uid, kind)),
                                                     real(uid, kind, *a, **kw))[1])
    status, _loc, body = users["a"].post_json("/api/run-apply", {},
                                              {"Sec-Fetch-Site": "same-origin"})
    assert status == 200
    assert any(k == "apply" for _u, k in seen), \
        "/api/run-apply did not enqueue - it is spawning a thread again"
    assert json.loads(body)["status"] in ("queued", "already_running")


# ── Request logging, through the real handler ────────────────────────────────
#
# tests/test_log.py proves log.py works. These prove app.py actually USES it -
# a request context that is never opened, or a status never recorded, would
# leave every unit test in that file passing and every production log line
# saying rid=- u=- 0.
#
# Read _wait_for_access before adding one of these. The access line is emitted
# by the SERVER thread after the response has been written, and the client
# returns as soon as it has the body, so asserting on caplog.records the
# instant get() returns is a race - one this suite lost on Windows on
# 2026-09-15 while passing every time on Linux.

def _wait_for_access(caplog, path, expected, timeout=3.0):
    """Wait for `expected` access lines for `path`, then return them.

    caplog.records is the capture handler's live list. Reading len() on it
    straight after a request can catch the server mid-emit - which is exactly
    what happened: the assertion saw one line while pytest's end-of-test
    report, rendered later from the SAME list, showed two.
    """
    marker = "%s ->" % path
    deadline = time.time() + timeout
    while True:
        lines = [r for r in caplog.records if marker in r.getMessage()]
        if len(lines) >= expected or time.time() >= deadline:
            # A short settle so "exactly N" means N and not "N so far".
            time.sleep(0.05)
            return [r for r in caplog.records if marker in r.getMessage()]
        time.sleep(0.01)


def test_a_real_request_produces_one_access_line_naming_the_user(stack, users, caplog):
    import logging
    with caplog.at_level(logging.INFO):
        status, _loc, _body = users["a"].get("/api/me")
        assert status == 200
        lines = _wait_for_access(caplog, "/api/me", 1)

    assert len(lines) == 1, [r.getMessage() for r in lines]
    assert "GET /api/me -> 200" in lines[0].getMessage()
    assert lines[0].uid not in (None, "-"), \
        "the access line does not know who made the request"


def test_lines_logged_during_a_request_carry_that_requests_id(stack, users, caplog):
    """The whole point: a hundred interleaved lines from ten threads, and the
    ones belonging to one request can be pulled out together."""
    import logging
    with caplog.at_level(logging.INFO):
        users["a"].get("/api/me")
        access = _wait_for_access(caplog, "/api/me", 1)
    assert access and access[0].rid and access[0].rid != "-"


def test_an_anonymous_request_is_logged_without_inventing_a_user(stack, caplog):
    import logging
    c = Client(stack["port"])
    with caplog.at_level(logging.INFO):
        c.get("/api/me")
        lines = _wait_for_access(caplog, "/api/me", 1)
    assert lines and lines[0].uid == "-"


def test_two_requests_get_two_different_ids(stack, users, caplog):
    import logging
    with caplog.at_level(logging.INFO):
        users["a"].get("/api/me")
        users["b"].get("/api/me")
        lines = _wait_for_access(caplog, "/api/me", 2)
    ids = [r.rid for r in lines]
    assert len(ids) == 2, ids
    assert ids[0] != ids[1]


def test_health_reports_a_real_database_round_trip_and_the_worker(stack, users):
    status, _loc, body = users["admin"].get("/api/health")
    assert status == 200
    payload = json.loads(body)
    assert payload["db_check"]["ok"] is True
    assert isinstance(payload["db_check"]["ms"], int)
    w = payload["worker"]
    assert "running" in w and "stuck" in w


def test_db_check_fails_when_the_database_cannot_answer(stack, monkeypatch):
    """Liveness is not usefulness. get_db() can hand back a pooled connection
    to a server that has gone away, which reads as healthy until the first real
    query - so db_check has to actually issue one.

    A first version of this test asserted only ok is True, and passed with the
    query deleted. The mutation check caught it.
    """
    app_module = stack["app"]

    class DeadConn:
        def execute(self, *a, **k):
            raise RuntimeError("server closed the connection unexpectedly")
        def close(self):
            pass

    monkeypatch.setattr(app_module.database, "get_db", lambda *a, **k: DeadConn())
    out = app_module.db_check()
    assert out["ok"] is False
    assert "server closed" in out["error"]


def test_worker_health_degrades_rather_than_raising(stack, monkeypatch):
    app_module = stack["app"]
    monkeypatch.setattr(app_module.worker, "health",
                        lambda: (_ for _ in ()).throw(RuntimeError("queue gone")))
    out = app_module.worker_health()
    assert "queue gone" in out["unavailable"]


# ── Onboarding lives in the app now ──────────────────────────────────────────

def test_a_new_account_lands_in_the_app_not_the_legacy_page(stack):
    """The whole point of moving the flow: a brand-new user's first screen is
    the product, not a server-rendered page that looks like a different one."""
    c = Client(stack["port"])
    status, location, _ = c.post_form("/register", {
        "name": "newbie", "email": "newbie@example.test",
        "password": "correct-horse-1", "password2": "correct-horse-1"})
    assert status == 302
    assert location == "/app", location


def test_the_legacy_onboarding_url_redirects_into_the_app(stack, users):
    """A bookmark or an old link must not drop someone back into the old UI."""
    status, location, _ = users["a"].get("/onboarding")
    assert status == 302
    assert location == "/app", location


def test_legacy_onboarding_can_be_brought_back_for_one_release(stack, users, monkeypatch):
    """LEGACY_UI=1 is the escape hatch. Without a way back, replacing a flow
    that every new signup hits is a one-way door."""
    monkeypatch.setattr(stack["app"], "LEGACY_UI", True)
    status, _location, body = users["a"].get("/onboarding")
    assert status == 200
    assert b"Upload your CV" in body


def test_onboarding_is_still_behind_auth(stack):
    status, location, _ = Client(stack["port"]).get("/onboarding")
    assert status == 302 and location == "/login"


def test_the_weekly_day_index_matches_what_the_scheduler_compares(stack, users):
    """The PWA sends search_day_of_week as an index into its own day list, and
    the scheduler compares it against datetime.weekday() - Python's
    0=Monday..6=Sunday. If the two conventions ever disagree, every weekly
    user's search silently moves by a day and nothing on screen says so."""
    from datetime import datetime
    assert datetime(2026, 9, 14).weekday() == 0, "2026-09-14 is a Monday"
    assert datetime(2026, 9, 20).weekday() == 6, "2026-09-20 is a Sunday"

    tsx = (pathlib.Path(stack["app"].BASE_DIR) / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    assert 'const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]' in tsx, \
        "the day picker is no longer Monday-first, but the scheduler still is"


def test_saving_a_weekly_schedule_keeps_the_chosen_day(stack, users):
    """A weekly user used to inherit whatever the column already held."""
    status, _loc, _b = users["a"].post_json(
        "/api/save-schedule",
        {"schedule_frequency": "weekly", "search_hour": 9, "search_day_of_week": 3},
        {"Sec-Fetch-Site": "same-origin"})
    assert status == 200
    _s, _l, body = users["a"].get("/api/me")
    me = json.loads(body)
    assert me["schedule_frequency"] == "weekly"
    assert me["search_day_of_week"] == 3, me.get("search_day_of_week")


def test_the_users_fixture_is_not_defeated_by_a_tripped_limiter(stack):
    """Other modules import `users`, so it runs once per module in one process
    from one IP. The register limiter is per-IP and process-global: the second
    module to ask got 429 on the third signup and every test in it errored at
    setup. Proven 2026-09-15 by the exact message - "register
    admin@example.test returned 429".

    This drives the condition directly: trip the limiter, then register the way
    the fixture does. Without the reset inside `users`, this fails.
    """
    import ratelimit
    for _ in range(30):
        ratelimit.record("register", "127.0.0.1")
    assert ratelimit.retry_after("register", "127.0.0.1") > 0, "could not trip the limiter"

    ratelimit.reset_all()          # what the fixture does
    c = Client(stack["port"])
    status, location, _ = c.post_form("/register", {
        "name": "late", "email": "late-arrival@example.test",
        "password": "correct-horse-1", "password2": "correct-horse-1"})
    assert status == 302 and location == "/app", f"status {status}"


def test_the_register_limiter_is_real_and_still_bites(stack):
    """The paired test. If reset_all() above passed because the limiter never
    limits anything, the guard would be worthless in both directions."""
    import ratelimit
    ratelimit.reset_all()
    seen_429 = False
    for i in range(40):
        status, _loc, _b = Client(stack["port"]).post_form("/register", {
            "name": f"flood{i}", "email": f"flood{i}@example.test",
            "password": "correct-horse-1", "password2": "correct-horse-1"})
        if status == 429:
            seen_429 = True
            break
    ratelimit.reset_all()
    assert seen_429, "registration is not rate limited at all - that is the real bug"
