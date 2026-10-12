"""m0012 touches exactly the reviewed rows, in exactly the reviewed state."""
import sqlite3

import migrations


def _db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    migrations.run(conn)
    return conn


def _add(conn, jid, uid, status, **kw):
    cols = {"id": jid, "user_id": uid, "title": "T", "company": "C", "url": "u%d" % jid,
            "status": status, **kw}
    conn.execute("INSERT INTO jobs (%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                 tuple(cols.values()))


def _row(conn, jid):
    return dict(conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone())


def test_reconcile_only_touches_reviewed_rows(tmp_path):
    conn = _db(tmp_path)
    conn.execute("DELETE FROM schema_migrations WHERE version=12")
    note = "Manually applied by user [auto-removed: already attempted]"
    _add(conn, 1757, 1, "rejected", rejected_by="system", applied_date="2026-07-07T10:37:46",
         notes=note, apply_status="failed")
    _add(conn, 1758, 1, "rejected", rejected_by="user", applied_date="2026-07-07",  # changed since review
         notes=note)
    _add(conn, 1322, 2, "rejected", rejected_by="system", applied_date="2026-06-29",  # another user's row
         notes=note)
    _add(conn, 601, 1, "applied", applied_via="bulk", notes="Marked applied manually (one-time queue cleanup)")
    _add(conn, 1348, 1, "approved", applied_via="bulk")                             # restored by hand: not listed
    _add(conn, 1318, 1, "applied", applied_via="manual")                            # not bulk any more
    conn.commit()

    migrations.m0012_reconcile_eran_backlog(conn)

    r = _row(conn, 1757)
    assert (r["status"], r["applied_via"], r["rejected_by"]) == ("applied", "manual", None)
    assert r["applied_date"] == "2026-07-07T10:37:46"
    assert r["notes"] == "Manually applied by user"
    assert _row(conn, 1758)["status"] == "rejected"
    assert _row(conn, 1322)["status"] == "rejected"

    r = _row(conn, 601)
    assert (r["status"], r["rejected_by"]) == ("rejected", "system")
    assert "bulk-marked, never applied" in r["notes"]
    assert _row(conn, 1348)["status"] == "approved"
    assert _row(conn, 1318)["status"] == "applied"


def test_reconcile_is_idempotent(tmp_path):
    conn = _db(tmp_path)
    _add(conn, 601, 1, "applied", applied_via="bulk", notes="x")
    conn.commit()
    migrations.m0012_reconcile_eran_backlog(conn)
    first = _row(conn, 601)["notes"]
    migrations.m0012_reconcile_eran_backlog(conn)
    assert _row(conn, 601)["notes"] == first
