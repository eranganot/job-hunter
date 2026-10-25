import pytest


@pytest.fixture(autouse=True)
def _fresh_ats_cache():
    """apply_engine caches each company's ATS postings for a few minutes.
    Tests stub _ats_get with different answers for the same slug, so every
    test starts with an empty cache."""
    import apply_engine
    apply_engine._ATS_CACHE.clear()
    yield
    apply_engine._ATS_CACHE.clear()
