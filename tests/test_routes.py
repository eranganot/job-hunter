"""
tests/test_routes.py — route-level + tenant-isolation harness.

Boots the real `app.Handler` on an ephemeral port against a throwaway SQLite DB
and drives it over real HTTP. This is the safety net every later phase of
EXECUTION_PLAN_PUBLIC_LAUNCH.md is verified against: before this file, the
7,500-line HTTP handler had zero tests and nothing proved that user B cannot
touch user A's data.

Nothing here talks to Gemini, Resend, Playwright or the network.
"""
import http.client
import itertools
import json
import os
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

    def get(self, path):
        return self._with_retry(lambda: self._get(path))

    def _get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path, headers=self._headers())
        resp = conn.getresponse()
        body = resp.read()
        self._capture_cookie(resp)
        conn.close()
        return resp.status, resp.getheader("Location"), body

    def post_json(self, path, payload):
        return self._with_retry(lambda: self._post_json(path, payload))

    def _post_json(self, path, payload):
        body = json.dumps(payload).encode()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", path, body=body,
                     headers=self._headers({"Content-Type": "application/json",
                                            "Content-Length": str(len(body))}))
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
