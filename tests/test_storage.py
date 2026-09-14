"""
tests/test_storage.py - CV bytes in the database, the volume as a cache.

The property under test is the one Phase 2d exists for: a box with an empty
uploads directory must still be able to serve, analyse and apply with a user's
CV. Every other test here supports that one.

The payload is deliberately nasty - null bytes, every byte value, a UTF-8 BOM,
a trailing NUL - because the failure this guards against is not "the file is
missing", it is "the file came back subtly different" after a trip through
placeholder translation and an engine's binary type.
"""
import os

import pytest

import db as database
import storage


# A byte string that would survive nothing text-shaped: it is not valid UTF-8,
# it contains NULs, and it ends with one (which a C-string truncation would eat).
NASTY = (b"%PDF-1.7\n" + bytes(range(256)) + b"\x00\x00\xef\xbb\xbf"
         + b"stream\r\n" + bytes(range(255, -1, -1)) + b"\nendstream\n%%EOF\x00")


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A real SQLite database and a real uploads directory, both throwaway."""
    dbfile = str(tmp_path / "jobs.db")
    uploads = str(tmp_path / "uploads")
    os.makedirs(uploads)

    monkeypatch.setattr(database, "DB_PATH", dbfile, raising=False)
    monkeypatch.setattr(database, "DATABASE_URL", None, raising=False)
    monkeypatch.setattr(database, "BACKEND_REFUSAL", None, raising=False)
    monkeypatch.delenv("DB_BACKEND", raising=False)
    database.init_db()

    monkeypatch.setattr(storage, "UPLOADS_DIR", uploads, raising=False)

    conn = database.get_db()
    conn.execute("INSERT INTO users (name, email, password_hash, salt) VALUES (?,?,?,?)",
                 ("Ada", "ada@example.test", "h", "s"))
    uid = conn.execute("SELECT id FROM users WHERE email=?", ("ada@example.test",)).fetchone()[0]
    conn.close()
    return uid, uploads


# ── The property Phase 2d exists for ──────────────────────────────────────────

def test_a_cold_cache_is_rebuilt_from_the_database(store):
    """A fresh container, or a lost volume. The CV must still be reachable."""
    uid, uploads = store
    storage.put(uid, NASTY, "resume.pdf")

    path = storage.cache_path(uid)
    os.unlink(path)                      # the volume is gone
    assert not os.path.exists(path)

    rebuilt = storage.file_path(uid)     # what every call site in app.py asks for
    assert rebuilt == path and os.path.exists(rebuilt)
    with open(rebuilt, "rb") as f:
        assert f.read() == NASTY, "the rebuilt file is not the file that was stored"


def test_bytes_survive_the_round_trip_exactly(store):
    uid, _ = store
    storage.put(uid, NASTY, "resume.pdf")
    assert storage.get_bytes(uid) == NASTY
    assert storage.verify(uid)["ok"] is True


def test_verify_reports_the_hash_it_actually_read(store):
    uid, _ = store
    storage.put(uid, NASTY, "resume.pdf")
    v = storage.verify(uid)
    assert v["stored"] == v["actual"] == storage.sha256(NASTY)
    assert v["size"] == v["read_size"] == len(NASTY)


def test_an_empty_cache_file_does_not_satisfy_file_path(store):
    """A truncated or half-written file is worse than a missing one."""
    uid, _ = store
    storage.put(uid, NASTY, "resume.pdf")
    open(storage.cache_path(uid), "wb").close()          # 0 bytes

    with open(storage.file_path(uid), "rb") as f:
        assert f.read() == NASTY


# ── Legacy uploads ────────────────────────────────────────────────────────────

def test_a_pre_2d_file_on_disk_is_adopted_into_the_database(store):
    """Users the backfill never reached must converge on their own."""
    uid, uploads = store
    legacy = os.path.join(uploads, str(uid), "cv.pdf")
    os.makedirs(os.path.dirname(legacy))
    with open(legacy, "wb") as f:
        f.write(NASTY)

    assert storage.meta(uid) is None                     # nothing in the database yet
    assert storage.get_bytes(uid) == NASTY               # served from disk
    assert storage.meta(uid) is not None, "the legacy file was never adopted"
    assert storage.verify(uid)["ok"] is True


def test_adoption_can_be_declined(store):
    uid, uploads = store
    legacy = os.path.join(uploads, str(uid), "cv.pdf")
    os.makedirs(os.path.dirname(legacy))
    with open(legacy, "wb") as f:
        f.write(NASTY)

    assert storage.get_bytes(uid, adopt=False) == NASTY
    assert storage.meta(uid) is None


def test_has_is_true_for_a_database_row_and_for_a_legacy_file(store):
    uid, uploads = store
    assert storage.has(uid) is False

    legacy = os.path.join(uploads, str(uid), "cv.pdf")
    os.makedirs(os.path.dirname(legacy))
    with open(legacy, "wb") as f:
        f.write(b"x")
    assert storage.has(uid) is True

    storage.delete(uid)
    assert storage.has(uid) is False


# ── Replacing, deleting, refusing ─────────────────────────────────────────────

def test_re_uploading_replaces_rather_than_duplicates(store):
    uid, _ = store
    storage.put(uid, b"first version", "old.pdf")
    storage.put(uid, NASTY, "new.pdf")

    conn = database.get_db()
    n = conn.execute("SELECT COUNT(*) FROM user_files WHERE user_id=?", (uid,)).fetchone()[0]
    conn.close()
    assert n == 1, "a second upload left two rows"
    assert storage.get_bytes(uid) == NASTY
    assert storage.meta(uid)["filename"] == "new.pdf"


def test_delete_removes_both_the_row_and_the_cache(store):
    uid, _ = store
    storage.put(uid, NASTY, "resume.pdf")
    path = storage.cache_path(uid)
    storage.delete(uid)

    assert storage.meta(uid) is None
    assert not os.path.exists(path)
    assert storage.get_bytes(uid) is None
    assert storage.file_path(uid) is None


def test_empty_and_oversized_files_are_refused(store):
    uid, _ = store
    with pytest.raises(ValueError):
        storage.put(uid, b"", "empty.pdf")
    with pytest.raises(ValueError):
        storage.put(uid, b"x" * (storage.MAX_BYTES + 1), "huge.pdf")


def test_deleting_the_user_deletes_their_files(store):
    """FK cascade: a removed account must not leave its CV behind."""
    uid, _ = store
    storage.put(uid, NASTY, "resume.pdf")

    conn = database.get_db()
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    left = conn.execute("SELECT COUNT(*) FROM user_files WHERE user_id=?", (uid,)).fetchone()[0]
    conn.close()
    assert left == 0, "the CV outlived the account it belonged to"


# ── Hygiene ───────────────────────────────────────────────────────────────────

def test_metadata_reads_do_not_pull_the_blob(store):
    """Existence checks run on page loads; they must not drag a PDF along."""
    uid, _ = store
    storage.put(uid, NASTY, "resume.pdf")
    row = storage.meta(uid)
    assert "content" not in row.keys(), "meta() selected the file contents"
    assert row["size"] == len(NASTY)


def test_writing_the_cache_leaves_no_partial_files(store):
    uid, uploads = store
    storage.put(uid, NASTY, "resume.pdf")
    os.unlink(storage.cache_path(uid))
    storage.file_path(uid)

    leftovers = [f for f in os.listdir(os.path.join(uploads, str(uid))) if f.endswith(".part")]
    assert leftovers == [], "a temp file survived: %s" % leftovers


def test_the_cache_path_is_unchanged_from_before_phase_2d(store):
    """Existing rows carry the old cv_path; the layout must not move under them."""
    uid, uploads = store
    assert storage.cache_path(uid) == os.path.join(uploads, str(uid), "cv.pdf")
