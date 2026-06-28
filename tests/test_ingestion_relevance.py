"""Geography + title relevance gate for externally-sourced (global) jobs."""
import pytest
pytest.importorskip("pydantic")

from ingestion.relevance import passes, passes_location, passes_title

USER_TITLES = ["Head of Product", "Senior Product Manager", "Director of Product"]
USER_LOCS = ["Tel Aviv", "Israel"]
USER_KW = ["AI", "personalization", "product"]


def test_global_us_pm_job_is_dropped():
    # right-ish title, WRONG location → dropped
    assert not passes("Senior Product Manager", "Seattle, WA, United States",
                      USER_TITLES, USER_LOCS, USER_KW)


def test_israel_pm_job_passes():
    assert passes("Senior Product Manager", "Herzliya, Israel",
                  USER_TITLES, USER_LOCS, USER_KW)
    assert passes("Group Product Manager", "Tel Aviv-Yafo",
                  USER_TITLES, USER_LOCS, USER_KW)


def test_remote_job_passes_when_remote_ok():
    assert passes("Head of Product", "Remote - EMEA", USER_TITLES, USER_LOCS, USER_KW)
    assert not passes("Head of Product", "Remote - EMEA", USER_TITLES, USER_LOCS,
                      USER_KW, remote_ok=False)


def test_wrong_role_in_israel_is_dropped():
    # right location, WRONG role → dropped
    assert not passes("Senior Software Engineer", "Tel Aviv, Israel",
                      USER_TITLES, USER_LOCS, USER_KW)
    assert not passes("Warehouse Associate", "Haifa, Israel",
                      USER_TITLES, USER_LOCS, USER_KW)


def test_product_noun_overlap_passes():
    # adjacent product role in Israel passes on key-noun overlap
    assert passes("VP Product", "Ramat Gan, Israel", USER_TITLES, USER_LOCS, USER_KW)


def test_unknown_location_defers_to_ai():
    assert passes_location("", USER_LOCS)          # empty → keep, let AI decide


def test_no_prefs_passes_everything():
    assert passes("Anything", "Anywhere", [], [], [])


def test_location_token_match_non_israel():
    assert passes_location("Berlin, Germany", ["Berlin"], remote_ok=False)
    assert not passes_location("Munich, Germany", ["Berlin"], remote_ok=False)
