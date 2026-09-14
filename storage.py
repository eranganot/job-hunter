"""
storage.py - user files (today: CVs). The database is the system of record;
the volume is a cache.

Phase 2d of EXECUTION_PLAN_PUBLIC_LAUNCH.md.

Until now a CV existed only as UPLOADS_DIR/<user_id>/cv.pdf on a Railway
volume. A volume belongs to one service in one environment: it is not part of
the database dump, it does not follow a rollback, and a redeploy without it
starts empty - the fault that hit production on 2026-06-23. Phase 2 could
therefore move every row to Postgres and still leave production pinned to one
machine's disk.

So the bytes now live in `user_files`, and the file on disk is a cache that any
box can rebuild from the database. The point of the cache is that ~10 call
sites in app.py do `open(cv_path, 'rb')` or hand a path to the apply engine's
browser, which genuinely needs a real file. `cv_file()` hands them one and
guarantees it exists; nothing downstream had to change shape.

Every stored blob carries its sha256, which is what makes "the database has the
file" a checkable claim rather than an assumption - the backfill and the tests
both verify a byte-for-byte round trip against it.
"""
import hashlib
import os
import tempfile

import db as database

KIND_CV = "cv"

# A CV is a PDF; production's largest is 387 KB. The cap is not about disk - it
# is so a malformed or hostile upload cannot put an arbitrarily large object in
# a row that ordinary queries may later select.
MAX_BYTES = 10 * 1024 * 1024

UPLOADS_DIR = None          # injected by app.py at startup


def set_uploads_dir(path: str):
    global UPLOADS_DIR
    UPLOADS_DIR = path


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cache_path(user_id, kind: str = KIND_CV) -> str:
    """Where the cached copy lives. Unchanged from the pre-2d layout, on purpose."""
    if not UPLOADS_DIR:
        raise RuntimeError("storage.set_uploads_dir() was never called")
    name = "cv.pdf" if kind == KIND_CV else ("%s.bin" % kind)
    return os.path.join(UPLOADS_DIR, str(user_id), name)


def _write_cache(path: str, data: bytes):
    """
    Write the cache copy atomically.

    Two requests can restore the same CV at once (a page load and the apply
    worker, say). Writing in place would let one reader see a half-written PDF;
    a temp file plus os.replace means a reader sees either the old file or the
    complete new one.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def put(user_id, data: bytes, filename: str = "cv.pdf",
        kind: str = KIND_CV, content_type: str = "application/pdf") -> dict:
    """Store bytes as the system of record, then refresh the cache copy."""
    if not data:
        raise ValueError("refusing to store an empty file")
    if len(data) > MAX_BYTES:
        raise ValueError("file is %d bytes; the limit is %d" % (len(data), MAX_BYTES))

    digest = sha256(data)
    conn = database.get_db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO user_files "
            "(user_id, kind, filename, content_type, content, size, sha256) "
            "VALUES (?,?,?,?,?,?,?)",
            (user_id, kind, filename, content_type, data, len(data), digest))
        conn.commit()
    finally:
        conn.close()

    # The cache is written second and its failure is not fatal: the bytes are
    # already durable, and cv_file() rebuilds the cache on the next read.
    path = cache_path(user_id, kind)
    try:
        _write_cache(path, data)
    except Exception as exc:
        print("[storage] cached copy not written for user %s: %s" % (user_id, exc))

    return {"path": path, "filename": filename, "size": len(data), "sha256": digest}


def meta(user_id, kind: str = KIND_CV):
    """Metadata only - deliberately never selects `content`."""
    conn = database.get_db()
    try:
        return conn.execute(
            "SELECT user_id, kind, filename, content_type, size, sha256, uploaded_date "
            "FROM user_files WHERE user_id=? AND kind=?", (user_id, kind)).fetchone()
    finally:
        conn.close()


def get_bytes(user_id, kind: str = KIND_CV, adopt: bool = True):
    """
    The file's bytes, or None.

    Database first. If the row is missing but a cached file exists, that is a
    pre-2d upload the backfill has not reached: adopt it into the database
    rather than pretending it is not there, so the estate converges even for a
    user nobody ran the backfill for.
    """
    conn = database.get_db()
    try:
        row = conn.execute("SELECT content FROM user_files WHERE user_id=? AND kind=?",
                           (user_id, kind)).fetchone()
    finally:
        conn.close()
    if row is not None and row["content"] is not None:
        return bytes(row["content"])

    path = cache_path(user_id, kind)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        data = f.read()
    if adopt and data:
        try:
            put(user_id, data, filename=os.path.basename(path), kind=kind)
            print("[storage] adopted legacy %s for user %s into the database" % (kind, user_id))
        except Exception as exc:
            # A read must never fail because the write-back failed.
            print("[storage] could not adopt legacy %s for user %s: %s" % (kind, user_id, exc))
    return data


def has(user_id, kind: str = KIND_CV) -> bool:
    if meta(user_id, kind) is not None:
        return True
    return os.path.exists(cache_path(user_id, kind))


def file_path(user_id, kind: str = KIND_CV):
    """
    A path that is readable right now, or None.

    This is what keeps the existing call sites working: they still get a path,
    and the apply engine still gets a real file to hand a browser. If the cache
    is cold - a fresh container, a lost volume, a restored backup - the file is
    rebuilt from the database first.
    """
    path = cache_path(user_id, kind)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    data = get_bytes(user_id, kind)
    if not data:
        return None
    try:
        _write_cache(path, data)
    except Exception as exc:
        print("[storage] could not rebuild the cache for user %s: %s" % (user_id, exc))
        return None
    return path


def delete(user_id, kind: str = KIND_CV):
    conn = database.get_db()
    try:
        conn.execute("DELETE FROM user_files WHERE user_id=? AND kind=?", (user_id, kind))
        conn.commit()
    finally:
        conn.close()
    try:
        os.unlink(cache_path(user_id, kind))
    except OSError:
        pass


def verify(user_id, kind: str = KIND_CV) -> dict:
    """
    Read the bytes back out of the database and re-hash them.

    Used by the backfill and by the tests. `stored` is what was written,
    `actual` is what came back; equal means the round trip through the driver,
    the placeholder translation and the engine's binary type lost nothing.
    """
    row = meta(user_id, kind)
    if row is None:
        return {"ok": False, "reason": "no row in user_files"}
    data = get_bytes(user_id, kind, adopt=False)
    if data is None:
        return {"ok": False, "reason": "row exists but no content came back"}
    actual = sha256(data)
    return {
        "ok": actual == row["sha256"] and len(data) == row["size"],
        "stored": row["sha256"], "actual": actual,
        "size": row["size"], "read_size": len(data),
        "filename": row["filename"],
    }
