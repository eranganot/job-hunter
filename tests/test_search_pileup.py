"""
tests/test_search_pileup.py - the search pile-up fix (2026-11-24, searchcache.py).

Production's 23 Nov 11:00 searches took 677-831 s each, three at a time, and a
new user's first search waited behind them. Each property below is one of the
three causes found in those logs:

  * jobs already in the user's list were sent to the AI again every day;
  * jobs scored below the bar were scored again the next day for the same profile;
  * 40-72 scoring calls per search ran one after another;
  * the queue was first in, first out, so a first search waited behind the batch.
"""
import threading
import time

import pytest

import db as database
import jobqueue
import searchcache


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "s.db"), raising=False)
    monkeypatch.setattr(database, "DATABASE_URL", None, raising=False)
    monkeypatch.setattr(database, "BACKEND_REFUSAL", None, raising=False)
    monkeypatch.delenv("DB_BACKEND", raising=False)
    database.init_db()
    c = database.get_db()
    for i in (1, 2, 3):
        c.execute("INSERT INTO users (id,name,email,password_hash,salt) VALUES (?,?,?,?,?)",
                  (i, "u%d" % i, "u%d@example.test" % i, "h", "s"))
    c.commit()
    yield c
    c.close()


def _job(n):
    return {"url": "https://boards.example/jobs/%d" % n, "job_title": "PM %d" % n, "company": "Co"}


# ── what is sent to the AI ───────────────────────────────────────────────────

def test_jobs_already_in_the_list_are_not_scored_again():
    jobs = [_job(i) for i in range(10)]
    known = {_job(1)["url"], _job(2)["url"], _job(3)["url"]}
    to_score, n_known, n_rej = searchcache.split_unscored(jobs, known, set())
    assert n_known == 3 and n_rej == 0
    assert [j["url"] for j in to_score] == [_job(i)["url"] for i in (0, 4, 5, 6, 7, 8, 9)]


def test_a_job_without_a_url_is_always_scored():
    to_score, n_known, _ = searchcache.split_unscored([{"url": "", "job_title": "x"}], {""}, {""})
    assert len(to_score) == 1 and n_known == 0


def test_below_the_bar_is_remembered_for_the_same_profile_only(conn):
    sig = searchcache.profile_signature("Target roles: PM", 1000)
    searchcache.remember_rejected(conn, 1, sig, [_job(1)["url"], _job(2)["url"]])
    searchcache.remember_rejected(conn, 1, sig, [_job(1)["url"]])         # idempotent
    assert searchcache.recently_rejected(conn, 1, sig) == {_job(1)["url"], _job(2)["url"]}
    other = searchcache.profile_signature("Target roles: Data Analyst", 1000)
    assert searchcache.recently_rejected(conn, 1, other) == set()          # titles changed: score again
    assert searchcache.recently_rejected(conn, 2, sig) == set()            # another user
    new_cv = searchcache.profile_signature("Target roles: PM", 2000)
    assert new_cv != sig                                                   # a new CV: score again


def test_the_memory_runs_out_after_the_window(conn):
    sig = searchcache.profile_signature("p", 0)
    old = searchcache._stamp(days_ago=searchcache.REJECT_DAYS + 1)
    conn.execute("INSERT INTO score_skips (user_id, url, sig, created_at) VALUES (?,?,?,?)",
                 (1, _job(9)["url"], sig, old))
    conn.commit()
    assert searchcache.recently_rejected(conn, 1, sig) == set()
    searchcache.prune(conn)
    assert conn.execute("SELECT COUNT(*) FROM score_skips").fetchone()[0] == 0


def test_the_memory_can_be_switched_off(conn, monkeypatch):
    sig = searchcache.profile_signature("p", 0)
    searchcache.remember_rejected(conn, 1, sig, [_job(1)["url"]])
    monkeypatch.setenv("JH_SCORE_SKIP_DAYS", "0")
    assert searchcache.recently_rejected(conn, 1, sig) == set()


def test_only_what_the_scorer_left_out_counts_as_rejected():
    batch = [_job(i) for i in range(5)]
    kept = [dict(_job(1)), dict(_job(3))]
    assert searchcache.rejected_in(batch, kept) == [_job(i)["url"] for i in (0, 2, 4)]


def test_an_unreliable_answer_is_marked_so_its_misses_are_not_remembered():
    # app.py appends a batch to the memory only when the answer is not Unreliable
    # (no JSON, or a truncated response salvaged object by object).
    assert isinstance(searchcache.Unreliable([_job(1)]), list)
    assert not isinstance([_job(1)], searchcache.Unreliable)


# ── how it is sent ───────────────────────────────────────────────────────────

def test_batches_run_several_at_a_time_and_come_back_in_order():
    jobs = [_job(i) for i in range(60)]                     # 3 batches of 20
    inside, peak, lock = [0], [0], threading.Lock()

    def score(batch):
        with lock:
            inside[0] += 1
            peak[0] = max(peak[0], inside[0])
        time.sleep(0.2)
        with lock:
            inside[0] -= 1
        return [batch[0]["url"]]

    t0 = time.time()
    out = searchcache.run_batches(jobs, score, size=20, parallel=3)
    assert out == [[_job(0)["url"]], [_job(20)["url"]], [_job(40)["url"]]]
    assert peak[0] == 3
    assert time.time() - t0 < 0.5                           # one batch's time, not three


def test_parallel_one_is_the_old_sequential_loop():
    order = []
    searchcache.run_batches([_job(i) for i in range(45)], lambda b: order.append(b[0]["url"]) or [],
                            size=20, parallel=1)
    assert order == [_job(0)["url"], _job(20)["url"], _job(40)["url"]]


def test_parallelism_is_bounded(monkeypatch):
    monkeypatch.setenv("JH_SCORE_PARALLEL", "50")
    assert searchcache.parallel_batches() == 6
    monkeypatch.setenv("JH_SCORE_PARALLEL", "0")
    assert searchcache.parallel_batches() == 1
    monkeypatch.setenv("JH_SCORE_PARALLEL", "x")
    assert searchcache.parallel_batches() == 3


# ── the queue ────────────────────────────────────────────────────────────────

def test_a_first_search_goes_ahead_of_the_scheduled_batch(conn):
    jobqueue.enqueue(1, "search")                                          # 11:00 batch
    jobqueue.enqueue(2, "search")
    first = jobqueue.enqueue(3, "search", priority=jobqueue.PRIORITY_FIRST)  # new user finishes setup
    run = jobqueue.claim(kinds=["search"], by="w1")
    assert run["id"] == first and run["user_id"] == 3
    nxt = jobqueue.claim(kinds=["search"], by="w2")
    assert nxt["user_id"] == 1                                             # then oldest first


def test_a_search_the_user_pressed_goes_ahead_of_scheduled_ones(conn):
    jobqueue.enqueue(1, "search")
    pressed = jobqueue.enqueue(2, "search", payload={"by_user": True}, priority=jobqueue.PRIORITY_USER)
    assert jobqueue.claim(kinds=["search"], by="w1")["id"] == pressed


def test_the_app_queues_first_and_pressed_searches_with_priority():
    src = open(__file__.replace("tests/test_search_pileup.py", "app.py"), encoding="utf-8").read()
    assert 'jobqueue.enqueue(user_id, "search", priority=jobqueue.PRIORITY_FIRST)' in src
    assert 'payload={"by_user": True},\n                                           priority=jobqueue.PRIORITY_USER)' in src
    assert "searchcache.split_unscored(all_raw, existing_urls, _rejected)" in src
    assert "for j in _unscored" in src                                     # Track B no longer re-scores the list
