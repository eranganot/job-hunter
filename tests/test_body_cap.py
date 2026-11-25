"""Request bodies are capped and server errors do not echo exception text
(test week security check, 2026-11-25: a 26 MB body to /login was read in full
and answered 500 with "Server error: 'utf-8' codec can't decode byte ...")."""
import http.client

from tests.test_routes import Client, stack, users  # noqa: F401


def _post(port, path, body, ctype="application/x-www-form-urlencoded"):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    c.request("POST", path, body=body, headers={"Content-Type": ctype})
    r = c.getresponse()
    out = r.status, r.read()
    c.close()
    return out


def test_an_oversized_body_is_refused_without_reading_it(stack):
    import app
    c = http.client.HTTPConnection("127.0.0.1", stack["port"], timeout=30)
    c.putrequest("POST", "/login")
    c.putheader("Content-Type", "application/x-www-form-urlencoded")
    c.putheader("Content-Length", str(app.MAX_BODY_BYTES + 1))
    c.endheaders()
    c.send(b"a" * 1024)                       # the rest never comes: the server must not wait for it
    r = c.getresponse()
    status, body = r.status, r.read()
    c.close()
    assert status == 413 and b"too large" in body


def test_bytes_that_are_not_utf8_get_the_login_page_not_a_500(stack):
    status, body = _post(stack["port"], "/login", b"\xd3\xff\xfe" * 100)
    assert status == 200 and b"codec" not in body


def test_an_unhandled_error_does_not_show_its_text(stack, monkeypatch):
    import app
    def boom(self):
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr(app.Handler, "_do_POST_inner", boom)
    status, body = _post(stack["port"], "/api/anything", b"{}", "application/json")
    assert status == 500 and b"secret internal detail" not in body and b"Something went wrong" in body


def test_a_cv_must_be_a_pdf_inside_not_only_by_name(stack, users):
    import json
    status, _, body = users["a"].post_json("/api/upload-cv", {"filename": "cv.pdf",
                                                                "data": "TVogbm90IGEgcGRm"})   # "MZ not a pdf"
    assert b"Only PDF files" in body, body
