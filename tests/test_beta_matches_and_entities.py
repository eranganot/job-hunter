"""Ticket 2026-11-11 (account 10): a Beta user saw 6 of 25 matches, and LinkedIn
titles showed "&amp;". Beta now shows every match; LinkedIn text is decoded and
the rows already stored are repaired by migrations 31 and 32."""
import re
import sqlite3

import entitlements
import migrations
import plans


def _db(upto):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    saved = migrations.MIGRATIONS
    migrations.MIGRATIONS = [m for m in saved if m[0] <= upto]
    try:
        migrations.run(c)
    finally:
        migrations.MIGRATIONS = saved
    return c


def _user(c, uid, plan, role="user"):
    c.execute("INSERT INTO users (id, name, email, password_hash, salt, created_date, role, plan, plan_source) "
              "VALUES (?,?,?,?,?,?,?,?,?)", (uid, "u", f"u{uid}@x.test", "h", "s", "2026-11-10", role, plan, plan))


def _job(c, uid, title, status="new", batch=1, score=50, company="Co", location="Tel Aviv"):
    c.execute("INSERT INTO jobs (user_id, title, company, location, url, status, search_batch, match_score, found_date) "
              "VALUES (?,?,?,?,?,?,?,?,?)", (uid, title, company, location, f"https://x.test/{title}", status, batch, score, "2026-11-10"))


def test_beta_shows_every_match_premium_keeps_six():
    assert plans.row("beta")["matches_per_search"] is None
    assert plans.row("premium")["matches_per_search"] == 6
    # everything else on Beta is still Premium's
    assert {k: v for k, v in plans.row("beta").items() if k not in ("label", "matches_per_search")} == \
        {k: v for k, v in plans.row("premium").items() if k not in ("label", "matches_per_search")}


def test_a_beta_search_locks_nothing():
    c = _db(32)
    _user(c, 1, "beta")
    for i in range(17):
        _job(c, 1, f"Head of Product {i}", score=90 - i)
    c.commit()
    user = dict(c.execute("SELECT * FROM users WHERE id=1").fetchone())
    assert entitlements.lock_matches_beyond_limit(c, user, 0, 1) == []
    assert c.execute("SELECT COUNT(*) FROM jobs WHERE status='new'").fetchone()[0] == 17


def test_migration_31_unlocks_beta_only():
    c = _db(30)
    _user(c, 1, "beta"); _user(c, 2, "free"); _user(c, 3, "premium")
    for uid in (1, 2, 3):
        for i in range(6):
            _job(c, uid, f"t{uid}-{i}")
        for i in range(19):
            _job(c, uid, f"l{uid}-{i}", status="locked")
    c.commit()
    migrations.run(c)
    locked = dict(c.execute("SELECT user_id, COUNT(*) FROM jobs WHERE status='locked' GROUP BY user_id").fetchall())
    assert locked.get(1) is None          # beta: all 19 visible
    assert locked == {2: 19, 3: 19}        # free/premium untouched (their batch is already full)
    assert c.execute("SELECT COUNT(*) FROM jobs WHERE user_id=1 AND status='new'").fetchone()[0] == 25


def test_migration_32_decodes_entities_in_stored_jobs():
    c = _db(31)
    _user(c, 1, "beta")
    _job(c, 1, "Head of Customer Experience &amp; Success")
    _job(c, 1, "Product Management Lead", company="AT&amp;T Israel R&amp;D Center")
    _job(c, 1, "R&D Manager")                      # a bare & is left alone
    c.commit()
    migrations.run(c)
    rows = {r[0]: r[1] for r in c.execute("SELECT title, company FROM jobs")}
    assert rows == {"Head of Customer Experience & Success": "Co",
                    "Product Management Lead": "AT&T Israel R&D Center",
                    "R&D Manager": "Co"}


def test_linkedin_parser_decodes_before_storing():
    # The parse is inline in app.py's search; pin that every captured field is decoded.
    src = open("app.py", encoding="utf-8").read()
    block = src[src.index("# -- LinkedIn public guest endpoint --"):src.index('print(f"[search] LinkedIn guest:')]
    assert "_html_li.unescape(_li_titles[_li_i])" in block
    assert "_html_li.unescape(_li_comps[_li_i])" in block
    assert "_html_li.unescape(_li_locs[_li_i])" in block
    assert not re.search(r"_li_titles\[_li_i\]\.strip\(\)", block)
