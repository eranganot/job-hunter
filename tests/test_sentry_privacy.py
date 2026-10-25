"""What actually leaves the app when an error is reported (errors.py).

Captures the envelope sentry_sdk would send instead of sending it, so this
checks the real payload, not the init arguments."""
import pytest

sentry_sdk = pytest.importorskip("sentry_sdk")


@pytest.fixture
def captured(monkeypatch):
    import errors
    from sentry_sdk.transport import Transport

    sent = []

    class Capture(Transport):
        def capture_envelope(self, env):
            for item in env.items:
                if item.payload.json:
                    sent.append(item.payload.json)

    real_init = sentry_sdk.init

    def init_with_capture(*a, **kw):
        kw["transport"] = Capture
        return real_init(*a, **kw)

    monkeypatch.setenv("JH_SENTRY_DSN", "https://key@o0.ingest.sentry.io/0")
    monkeypatch.setattr(sentry_sdk, "init", init_with_capture)
    monkeypatch.setattr(errors, "_state", "unconfigured")
    monkeypatch.setattr(errors, "_client", None)
    assert errors.init() == "on"
    yield errors, sent
    sentry_sdk.get_client().close()
    sentry_sdk.init(dsn=None)


def test_reported_errors_carry_no_local_variables(captured):
    errors, sent = captured

    # Built at runtime: Sentry also ships the source lines around each frame,
    # so a literal here would be found in the code context, not in the
    # variables. In the app these values come from the database, never source.
    email, cv, tok = "someone" + "@example.test", "CV" + " text", "SECRET-" + "BOT-TOKEN"

    def handler():
        user = {"id": 1, "email": email, "cv_summary": cv}  # noqa: F841
        telegram_token = tok  # noqa: F841
        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            assert errors.capture(exc, where="test", path="/api/x?q=secret", user_id=1)

    handler()
    sentry_sdk.flush()
    assert sent, "nothing was captured"
    blob = repr(sent)
    for leaked in (email, cv, tok):
        assert leaked not in blob, f"{leaked!r} reached the Sentry payload"
    frames = sent[0]["exception"]["values"][0]["stacktrace"]["frames"]
    assert frames and all("vars" not in f for f in frames)
    # What diagnosis needs is still there: the frame, its line, the tags.
    assert any(f.get("function") == "handler" for f in frames)
    assert sent[0]["tags"]["where"] == "test"
    assert sent[0]["tags"]["path"] == "/api/x"          # query string dropped
    assert sent[0]["user"] == {"id": 1}
