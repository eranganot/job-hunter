"""
tests/test_connections_closed.py - every database connection is closed on every path.

On SQLite a connection an exception skipped cost nothing. On the Postgres pool
it is a slot gone until the process restarts, and on 2026-09-25 nine of them
took production down. The fix (2026-09-28) wrapped all ~90 call sites in
`try: ... finally: conn.close()`; this test keeps it that way for the next one.

It reads the source rather than running it, so it covers paths no test drives.
A connection counts as closed when it is:
  * assigned and then followed by `try: ... finally: <var>.close()`, or
  * assigned inside a try whose finally closes it, or
  * assigned inside a `try/except` whose very next statement is such a try
    (the /api/health shape: get the connection or answer 503, then use it).
Anything else fails - including `get_db().execute(...)`, which never closes.

dbdriver.release_thread_connections() still catches what slips through at
runtime; this is the check that stops it slipping through in the first place.
"""
import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = ["app.py", "auth.py", "db.py", "jobqueue.py", "storage.py",
         "scripts/encrypt_credentials.py", "scripts/import_cv_files.py"]
GETTERS = {"get_db", "_get_db"}


def _is_getter(node):
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)) in GETTERS


def _link(tree):
    for n in ast.walk(tree):
        for field, val in ast.iter_fields(n):
            kids = val if isinstance(val, list) else [val]
            for c in kids:
                if isinstance(c, ast.AST):
                    c._parent, c._field = n, field


def _closes(try_node, var):
    for s in try_node.finalbody:
        for x in ast.walk(s):
            if (isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute)
                    and x.func.attr == "close" and isinstance(x.func.value, ast.Name)
                    and x.func.value.id == var):
                return True
    return False


def _next_sibling(node):
    lst = getattr(node._parent, node._field, None)
    if isinstance(lst, list) and node in lst:
        i = lst.index(node)
        return lst[i + 1] if i + 1 < len(lst) else None
    return None


def _guarded(assign, var):
    nxt = _next_sibling(assign)
    if isinstance(nxt, ast.Try) and _closes(nxt, var):
        return True
    p = assign._parent
    while p is not None and not isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
        if isinstance(p, ast.Try):
            if p.finalbody and _closes(p, var):
                return True
            if assign in p.body or any(assign in ast.walk(s) for s in p.body):
                nxt = _next_sibling(p)
                if isinstance(nxt, ast.Try) and _closes(nxt, var):
                    return True
        p = getattr(p, "_parent", None)
    return False


def unclosed_sites(path):
    src = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    _link(tree)
    bad = []
    for n in ast.walk(tree):
        if not _is_getter(n):
            continue
        p = n._parent
        if isinstance(p, ast.Assign) and len(p.targets) == 1 and isinstance(p.targets[0], ast.Name):
            if not _guarded(p, p.targets[0].id):
                bad.append("%s:%d %s" % (path, n.lineno, ast.unparse(p)[:80]))
        elif isinstance(p, ast.BoolOp):
            # `conn = conn or get_db()` with an `own` flag (jobqueue.runs_today):
            # the enclosing assignment must still be closed in a finally.
            a = getattr(p, "_parent", None)
            if not (isinstance(a, ast.Assign) and isinstance(a.targets[0], ast.Name)
                    and _guarded(a, a.targets[0].id)):
                bad.append("%s:%d %s" % (path, n.lineno, ast.unparse(p)[:80]))
        elif isinstance(p, ast.Return) and path == "db.py":
            continue    # the getter's own implementation hands the connection out
        else:
            bad.append("%s:%d %s (not assigned, so never closed)"
                       % (path, n.lineno, ast.unparse(p)[:80]))
    return bad


@pytest.mark.parametrize("path", FILES)
def test_every_connection_is_closed_in_a_finally(path):
    assert unclosed_sites(path) == []


def test_the_check_can_fail(tmp_path, monkeypatch):
    """Validate the instrument: both leak shapes from 2026-09 must be caught."""
    (tmp_path / "leaky.py").write_text(
        "import db as database\n"
        "def a():\n"
        "    conn = database.get_db()\n"
        "    conn.execute('x')\n"
        "    conn.close()\n"
        "def b():\n"
        "    return database.get_db().execute('x').fetchone()\n"
        "def ok():\n"
        "    conn = database.get_db()\n"
        "    try:\n"
        "        conn.execute('x')\n"
        "    finally:\n"
        "        conn.close()\n")
    import sys
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    found = unclosed_sites("leaky.py")
    assert len(found) == 2, found
    assert "leaky.py:3" in found[0] or "leaky.py:3" in found[1]
