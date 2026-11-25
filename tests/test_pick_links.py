"""Launch item S3 (row 17h): Noa's picks roles link through /r/<id>, which counts the
click per role, family and place and sends the reader on to the employer's page."""
import noaspicks
from tests.test_routes import Client, stack, users  # noqa: F401

LINK = {"url": "https://boards.greenhouse.io/lumen/jobs/1", "title": "Senior PM", "company": "Lumen",
        "family": "product", "source": "facebook", "medium": "group"}


def _conn(stack):
    return stack["db"].get_db()


def test_r_link_counts_the_click_and_goes_to_the_employer(stack):
    rid = noaspicks.link_id(LINK["url"], "product", "facebook", "group")
    conn = _conn(stack)
    try:
        noaspicks.save_links(conn, [dict(LINK, id=rid)])
        noaspicks.save_links(conn, [dict(LINK, id=rid)])          # building the post again: stored once
        assert conn.execute("SELECT COUNT(*) FROM pick_links WHERE id=?", (rid,)).fetchone()[0] == 1
    finally:
        conn.close()
    c = Client(stack["port"])
    for _ in range(2):
        status, loc, _b = c.get("/r/" + rid)
        assert status == 302 and loc == LINK["url"]
    conn = _conn(stack)
    try:
        assert conn.execute("SELECT clicks FROM pick_links WHERE id=?", (rid,)).fetchone()[0] == 2
        got = noaspicks.clicks(conn)
        assert got["by_family"]["product"] >= 2 and got["by_place"]["facebook/group"] >= 2
        assert got["top_roles"][0]["company"] == "Lumen"
    finally:
        conn.close()


def test_unknown_or_odd_ids_never_redirect_off_site(stack):
    c = Client(stack["port"])
    for bad in ("/r/nosuchid", "/r/https:%2F%2Fevil.example", "/r/" + "a" * 40):
        status, loc, _b = c.get(bad)
        assert status == 302 and "evil.example" not in (loc or "") and "greenhouse" not in (loc or "")


def test_ids_are_stable_per_role_and_place():
    a = noaspicks.link_id(LINK["url"], "product", "facebook", "group")
    assert a == noaspicks.link_id(LINK["url"], "product", "facebook", "group") and len(a) == 8
    assert a != noaspicks.link_id(LINK["url"], "product", "linkedin", "post")
