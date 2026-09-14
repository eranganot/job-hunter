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
    os.makedirs(os.environ["UPLOADS_DIR"], exist_ok=True)

    database.init_db()

    # Registration notifies the admin by email; keep the suite offline.
    app_module.notify_admin_new_user = lambda **_kw: None
    app_module.deliver_notification = lambda *_a, **_kw: None

    srv = _Server(("127.0.0.1", 0), app_module.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    yield {"port": port, "db": database, "app": app_module}

    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="module")
def users(stack):
    """Two registered users (A and B) plus the admin, each with a live session."""
    made = {}
    for key, email in (("a", "alice@example.test"),
                       ("b", "bob@example.test"),
                       ("admin", "admin@example.test")):
        c = Client(stack["port"])
        status, location, _ = c.post_form("/register", {
            "name": key, "email": email,
            "password": "correct-horse-1", "password2": "correct-horse-1"})
        assert status == 302, f"register {email} returned {status}"
        assert location == "/onboarding"
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
    status, location, _ = users["b"].get("/admin")
    assert status == 302 and location == "/dashboard"
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
