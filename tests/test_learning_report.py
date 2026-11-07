"""Step 12: the learning check over decided jobs."""
import sqlite3

import learningreport


def _db(rows):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE jobs (user_id INT, status TEXT, rejected_by TEXT, notes TEXT, "
              "feedback_penalty INT, feedback_reason TEXT, match_score INT)")
    c.executemany("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", rows)
    return c


def test_demoted_jobs_approved_less_reads_as_working():
    rows = ([(1, "approved", None, "", 0, "", 90)] * 30 + [(1, "rejected", "user", "Not a good fit", 0, "", 60)] * 10
            + [(1, "approved", None, "", 20, "You've passed on this company before", 70)] * 5
            + [(1, "rejected", "user", "x", 20, "You've passed on this company before", 40)] * 20
            + [(1, "rejected", "system", "expired", 0, "", 50)] * 50          # not his decision
            + [(2, "approved", None, "", 0, "", 90)] * 7)                     # another user
    r = learningreport.report(_db(rows), user_id=1)
    assert r["decided"] == 65
    pen = {x["group"]: x for x in r["by_penalty"]}
    assert pen["not demoted"]["approval_rate"] == 75.0 and pen["demoted"]["approval_rate"] == 20.0
    assert r["verdict"].startswith("Penalties work")
    reasons = {x["group"]: x for x in r["by_reason"]}
    assert reasons["You've passed on this company before"]["decided"] == 25
    scores = [x["group"] for x in r["by_score"]]
    assert scores == ["85+", "70-84", "50-69", "under 50"]


def test_applied_elsewhere_is_an_approval_and_an_application():
    rows = [(1, "rejected", "user", "Already applied elsewhere", 0, "", 80),
            (1, "applied", None, "", 0, "", 80), (1, "approved", None, "", 0, "", 80)]
    r = learningreport.report(_db(rows), user_id=1)
    assert r["approval_rate"] == 100.0 and r["apply_rate"] == 66.7


def test_too_few_demoted_decisions_is_said_plainly():
    r = learningreport.report(_db([(1, "approved", None, "", 0, "", 90)]), user_id=1)
    assert r["verdict"].startswith("Not enough demoted jobs")
