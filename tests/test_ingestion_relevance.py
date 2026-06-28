"""
Geography + title relevance gate.

Models the real user case: targets Israel (Tel Aviv) + 'Hybrid' work mode,
did NOT opt into remote. So Israel-office jobs (incl. hybrid-in-Israel) pass;
'Hybrid - London' and 'Remote - US' are dropped.
"""
import pytest
pytest.importorskip("pydantic")

from ingestion.relevance import passes, passes_location, passes_title

TITLES = ["Head of Product", "Senior Product Manager", "Director of Product"]
# user listed an Israeli city + a work-mode word, NOT remote
LOCS = ["Tel Aviv", "Hybrid"]
KW = ["AI", "personalization", "product"]


# ---- geography ----------------------------------------------------------

def test_israel_office_jobs_pass():
    assert passes("Senior Product Manager", "Herzliya, Israel", TITLES, LOCS, KW)
    assert passes("Group Product Manager", "Tel Aviv-Yafo, Israel", TITLES, LOCS, KW)
    assert passes("Head of Product", "Israel (Hybrid)", TITLES, LOCS, KW)


def test_hybrid_abroad_is_dropped():
    # 'hybrid' is a work mode, NOT a location — a London hybrid job must NOT match
    assert not passes("Senior Product Manager", "Hybrid - London, UK", TITLES, LOCS, KW)


def test_remote_anywhere_dropped_when_user_didnt_opt_in():
    # user listed 'Hybrid', never 'Remote' → remote-anywhere jobs are dropped
    assert not passes("Head of Product", "Remote - United States", TITLES, LOCS, KW)
    assert not passes("Head of Product", "Remote - EMEA", TITLES, LOCS, KW)


def test_foreign_office_dropped():
    assert not passes("Senior Product Manager", "Seattle, WA, United States",
                      TITLES, LOCS, KW)


def test_remote_passes_only_if_user_lists_remote():
    locs_remote = ["Tel Aviv", "Remote"]
    assert passes("Head of Product", "Remote - Europe", TITLES, locs_remote, KW)


def test_unknown_location_defers_to_ai():
    assert passes_location("", LOCS)          # empty → keep, let the scorer decide


# ---- title --------------------------------------------------------------

def test_wrong_role_in_israel_is_dropped():
    assert not passes("Senior Software Engineer", "Tel Aviv, Israel", TITLES, LOCS, KW)
    assert not passes("Warehouse Associate", "Haifa, Israel", TITLES, LOCS, KW)


def test_product_variants_pass():
    assert passes("VP Product", "Ramat Gan, Israel", TITLES, LOCS, KW)
    assert passes("Sr. Product Manager", "Tel Aviv, Israel", TITLES, LOCS, KW)


def test_no_prefs_passes_everything():
    assert passes("Anything", "Anywhere", [], [], [])
