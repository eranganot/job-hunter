"""Step 16: board-first search."""
import sqlite3

import boardsearch as bs


def _db(rows):
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE jobs (company TEXT, url TEXT)")
    db.execute("CREATE TABLE company_boards (company_key TEXT PRIMARY KEY, boards TEXT NOT NULL, page TEXT, checked_at TEXT)")
    db.executemany("INSERT INTO jobs VALUES (?,?)", rows)
    db.execute("INSERT INTO company_boards VALUES ('echo', '[[\"ashby\", \"echo.ai\"]]', '', '')")
    db.execute("INSERT INTO company_boards VALUES ('nobody', '[]', '', '')")
    db.commit()
    return db


def test_known_boards_come_from_stored_links_and_the_careers_map():
    db = _db([("Taboola", "https://boards.greenhouse.io/taboola/jobs/1"),
              ("Taboola", "https://boards.greenhouse.io/taboola/jobs/2"),
              ("Taboola Inc", "https://boards.greenhouse.io/taboola/jobs/3"),
              ("Coralogix", "https://www.comeet.com/jobs/coralogix/06.004/be/0E.C68"),
              ("Acme", "https://www.linkedin.com/jobs/view/9")])
    got = bs.known_boards(db)
    assert ("greenhouse", "taboola", "Taboola") in got                    # most-used name
    assert ("comeet", "coralogix/06.004", "Coralogix") in got
    assert ("ashby", "echo.ai", "echo") in got                            # found by the careers-map
    assert not any(ats == "linkedin" for ats, _r, _c in got)


def _fake_get(url):
    if "greenhouse" in url:
        return {"jobs": [
            {"title": "Senior Product Manager", "location": {"name": "Tel Aviv, Israel"},
             "absolute_url": "https://boards.greenhouse.io/acme/jobs/1", "content": "&lt;p&gt;Own the roadmap&lt;/p&gt;"},
            {"title": "Senior Product Manager", "location": {"name": "New York, NY"},
             "absolute_url": "https://boards.greenhouse.io/acme/jobs/2"},
            {"title": "Backend Engineer", "location": {"name": "Tel Aviv"},
             "absolute_url": "https://boards.greenhouse.io/acme/jobs/3"}]}
    if "lever" in url:
        return None                                                         # did not answer
    return {"jobs": []}


def test_search_keeps_only_matching_israeli_postings_with_their_direct_link():
    bs._CACHE.clear()
    jobs, st = bs.search(None, lambda t: "product" in t.lower(), get=_fake_get,
                         boards=[("greenhouse", "acme", "Acme"), ("lever", "gone", "Gone")])
    assert [(j["url"], j["source"], j["company"]) for j in jobs] == [
        ("https://boards.greenhouse.io/acme/jobs/1", "board-greenhouse", "Acme")]
    assert "Own the roadmap" in jobs[0]["full_description"]
    assert st["postings"] == 3 and st["kept"] == 1 and st["not_reached"] == 0
    assert ("greenhouse", "acme") in bs._CACHE and ("lever", "gone") not in bs._CACHE   # no answer: not cached


def test_a_slow_board_is_counted_not_waited_for():
    import time
    bs._CACHE.clear()

    def slow(url):
        time.sleep(2)
        return {"jobs": []}
    _jobs, st = bs.search(None, lambda t: True, get=slow, budget_s=0.3, boards=[("greenhouse", "slow", "Slow")])
    assert st["not_reached"] == 1 and st["asked"] == 0
