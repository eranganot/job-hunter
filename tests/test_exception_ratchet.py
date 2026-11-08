"""Step 7: broad exception handlers may not swallow errors silently - and the
number that still do can only go down.

A handler is SILENT when it catches Exception (or everything) and neither logs,
re-raises, nor uses the exception. That shape hid real outages in this repo:
eight dead Workday tenants, a Meta source that never returned a job, five
Comeet slugs answering 404 and Indeed answering 401 on every search, and a
sync route reporting 0 inserted on Postgres - each behind `except Exception:
pass`, each found only by probing by hand (STATUS, 2026-11-05).

The baseline below is the count per file on 2026-11-05 after the Step 7 fixes.
Many remaining ones are deliberate (a JSON-parse fallback, "health must never
fail over a statistic"). A file may not GAIN one: new code logs at WARNING or
ERROR, or re-raises. When you remove one, lower the number here.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {"tests", "node_modules", "web", "web_bundle", "scripts", ".git", "__pycache__"}
LOGGY = ("print", "log", "warning", "error", "exception", "capture", "info", "debug",
         "critical", "_mark_lookup_failure", "_alert", "fail")

BASELINE = {
    "app.py": 36,
    "apply_engine.py": 25,
    "db.py": 10,
    "dbdriver.py": 5,
    "ingestion/models.py": 4,
    "migrations.py": 3,
    "entitlements.py": 3,
    "errors.py": 2,
    "linkcheck.py": 2,
    "log.py": 2,
    "ai_analysis.py": 2,
    "schedlock.py": 2,
    "ingestion/dedup.py": 2,
    "ingestion/http_client.py": 2,
    "ingestion/credits.py": 2,
    "ingestion/adapters/managed.py": 2,
    "worker.py": 1,
    "links.py": 1,
    "gemini.py": 1,
    "workday.py": 1,
    "ingestion/pipeline.py": 1,
    "ingestion/relevance.py": 1,
    "ingestion/adapters/bigtech.py": 1,
}


def _files():
    for p in ROOT.rglob("*.py"):
        if not SKIP_DIRS.intersection(p.relative_to(ROOT).parts):
            yield p


def _is_broad(h):
    if h.type is None:
        return "bare"
    names = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
    for n in names:
        if (isinstance(n, ast.Name) and n.id in ("Exception", "BaseException")) or \
                (isinstance(n, ast.Attribute) and n.attr == "Exception"):
            return "broad"
    return None


def _silent(h):
    if any(isinstance(n, ast.Raise) for n in ast.walk(h)):
        return False
    for c in ast.walk(h):
        if isinstance(c, ast.Call):
            f = c.func
            name = (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")) or ""
            if any(k in name.lower() for k in LOGGY):
                return False
    if h.name and any(isinstance(n, ast.Name) and n.id == h.name for n in ast.walk(h)):
        return False
    return True


def _audit():
    silent, bare = {}, []
    for p in _files():
        rel = p.relative_to(ROOT).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        for h in ast.walk(tree):
            if not isinstance(h, ast.ExceptHandler):
                continue
            kind = _is_broad(h)
            if kind == "bare":
                bare.append("%s:%d" % (rel, h.lineno))
            elif kind and _silent(h):
                silent[rel] = silent.get(rel, 0) + 1
    return silent, bare


def test_no_bare_except_in_the_app():
    _silent_counts, bare = _audit()
    assert not bare, "bare `except:` (catches KeyboardInterrupt and SystemExit too): %s" % bare


def test_no_file_gains_a_silent_broad_handler():
    silent, _bare = _audit()
    grew = {f: (n, BASELINE.get(f, 0)) for f, n in silent.items() if n > BASELINE.get(f, 0)}
    assert not grew, (
        "these files gained an `except Exception` that neither logs, re-raises nor uses the "
        "error - log it at WARNING/ERROR (it reaches Sentry) or narrow it: %s (now, baseline)" % grew)


def test_the_baseline_is_kept_honest():
    """A fixed handler should lower the number, so the ratchet keeps its grip."""
    silent, _bare = _audit()
    slack = {f: (silent.get(f, 0), n) for f, n in BASELINE.items() if silent.get(f, 0) < n}
    assert not slack, "lower BASELINE in this file to the new counts: %s (now, baseline)" % slack


# ── what Step 7 made visible ───────────────────────────────────────────────

def test_a_failing_legacy_source_reports_in_health():
    import urllib.error
    import app
    from ingestion import integration
    t = app._SourceTally("indeed")
    t.fail(urllib.error.HTTPError("u", 401, "x", {}, None))
    t.fail(urllib.error.HTTPError("u", 401, "x", {}, None))
    t.ok(4)
    t.kept = 1
    t.done()
    h = integration.source_health()["indeed"]
    assert h["status"] == "degraded" and "2 of 3 requests failed: HTTP 401" in h["message"]
    t2 = app._SourceTally("sparkhire")
    t2.fail(TimeoutError())
    t2.done()
    assert integration.source_health()["sparkhire"]["status"] == "error"


def test_sync_reports_what_it_really_inserted(stack, users, monkeypatch):  # noqa: F811
    """It counted with `SELECT changes()` (SQLite-only): on Postgres every sync
    reported 0. NOTE: these routes sit behind the session gate, so they need a
    session AND the key - relay.py (key only, in the query string) has never
    matched them; recorded in STATUS 2026-11-05, to be redesigned with SecretJobs."""
    import json
    app = stack["app"]
    monkeypatch.setattr(app, "SYNC_API_KEY", "k")
    jobs = [{"user_id": 1, "title": "Sync PM", "company": "Sync Co", "url": "https://sync.example/%d" % i}
            for i in (1, 2, 1)]                                                   # one duplicate
    st, _l, body = users["admin"].post_json("/api/sync/jobs", {"api_key": "k", "jobs": jobs})
    assert st == 200
    assert json.loads(body).get("inserted") == 2


from tests.test_routes import stack, users  # noqa: E402,F401
