import pytest


@pytest.fixture(autouse=True)
def _fresh_ats_cache():
    """apply_engine caches each company's ATS postings for a few minutes.
    Tests stub _ats_get with different answers for the same slug, so every
    test starts with an empty cache."""
    import apply_engine
    import links
    apply_engine._ATS_CACHE.clear()
    links._COMEET_CACHE.clear()
    yield
    apply_engine._ATS_CACHE.clear()
    links._COMEET_CACHE.clear()


@pytest.fixture(autouse=True)
def _no_live_careers_discovery(monkeypatch):
    """links.discover_boards guesses company websites and fetches them. The
    suite stays offline: every test gets "no board found" unless it passes its
    own discover_fetch (the careers-map tests do)."""
    import links
    real = links.discover_boards

    def offline(company, fetch=None, **kw):
        return real(company, fetch=fetch or (lambda url: (None, "")), **kw)
    monkeypatch.setattr(links, "discover_boards", offline)
    yield
