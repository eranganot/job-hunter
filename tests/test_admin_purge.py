"""Admin "delete now" for a disabled account (2026-11-25)."""
import json

from tests.test_routes import Client, _user_id, stack, users  # noqa: F401


def test_purge_needs_admin_a_disabled_target_and_never_an_admin(stack, users):
    db = stack["db"]
    a = _user_id(db, "alice@example.test")
    admin = _user_id(db, "admin@example.test")
    status, _, _ = users["b"].post_json(f"/api/admin/users/{a}/purge", {})
    assert status in (302, 403)
    status, _, body = users["admin"].post_json(f"/api/admin/users/{a}/purge", {})
    assert status == 400 and b"Disable the account first" in body
    status, _, body = users["admin"].post_json(f"/api/admin/users/{admin}/purge", {})
    assert status == 400
    users["admin"].post_json(f"/api/admin/users/{a}/toggle", {})
    status, _, body = users["admin"].post_json(f"/api/admin/users/{a}/purge", {})
    assert status == 200 and json.loads(body)["removed"]["users"] == 1
    c = db.get_db()
    try:
        assert c.execute("SELECT COUNT(*) FROM users WHERE id=?", (a,)).fetchone()[0] == 0
    finally:
        c.close()
    status, _, _ = users["admin"].post_json(f"/api/admin/users/{a}/purge", {})
    assert status == 404
