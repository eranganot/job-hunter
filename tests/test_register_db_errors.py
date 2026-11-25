"""Sign-up never shows the database's text (test week security check, 2026-11-25).

On Postgres a duplicate email showed 'duplicate key value violates unique constraint
"users_email_key" DETAIL: Key (email)=(...)' on the sign-up page: the friendly-message
check matched SQLite's "UNIQUE" only."""
import auth


class _Boom:
    def __init__(self, msg): self.msg = msg
    def execute(self, sql, *a):
        if sql.lstrip().upper().startswith("INSERT INTO USERS"):
            raise Exception(self.msg)
        class R:
            def fetchone(self): return None
        return R()
    def commit(self): pass
    def close(self): pass


def _create(monkeypatch, msg):
    monkeypatch.setattr(auth, "_get_db", lambda: _Boom(msg))
    monkeypatch.setattr(auth, "_signup_blocked", lambda *a: None)
    return auth.create_user("x", "a@b.test", "correct-horse-9")


def test_postgres_duplicate_reads_as_already_exists(monkeypatch):
    uid, err = _create(monkeypatch, 'duplicate key value violates unique constraint "users_email_key"\n'
                                    'DETAIL:  Key (email)=(a@b.test) already exists.')
    assert uid is None and err == "An account with that email already exists."


def test_any_other_database_error_is_not_shown(monkeypatch):
    uid, err = _create(monkeypatch, 'relation "users" does not exist <script>')
    assert uid is None and "relation" not in err and "<" not in err


def test_error_block_escapes():
    import app
    assert app.error_block("<script>x</script>") == '<div class="err">&lt;script&gt;x&lt;/script&gt;</div>'
