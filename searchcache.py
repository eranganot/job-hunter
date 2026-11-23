"""
searchcache.py - spend a search's AI time only on jobs the user has not seen.

Found on 2026-11-24 from production's own logs of the 23 Nov 11:00 searches
(STATUS.md has the full evidence): one search took 677-831 s, of which about
90 s was collecting jobs and 8-10 minutes was scoring them - 40 to 72 Gemini
calls of 20 jobs each, made one after another. User 3 had 778 jobs scored,
232 passed, and 5 were new; the rest were already in the user's list and were
thrown away at insert. Every board, Workday, LinkedIn and TechMap job was
re-scored every day, because only the paid pool and the external sources were
checked against the user's stored jobs before scoring.

Three things here, each used by app.py's search:

  * `split_unscored` drops what the user already has (any status, any age -
    the same set the insert step dedups against) and what was scored below the
    bar for the same profile in the last `REJECT_DAYS` days.
  * `remember_rejected` writes those below-the-bar jobs to `score_skips`, keyed
    by a signature of everything the score depends on, so a change of titles,
    skills, places or CV scores them again.
  * `run_batches` scores batches a few at a time instead of one at a time.

Only batches the AI actually answered are remembered: a batch that fell back to
keywords or came back unreadable is scored again next time.
"""
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import log as _log
print = _log.make_print(__name__)  # noqa: A001 - see log.py


REJECT_DAYS = 14
FMT = "%Y-%m-%d %H:%M:%S"


def _stamp(days_ago=0, now=None):
    """UTC text stamp; written and compared as text so SQLite and Postgres agree."""
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=days_ago)).strftime(FMT)


def reject_days():
    try:
        return max(0, int(os.environ.get("JH_SCORE_SKIP_DAYS", REJECT_DAYS)))
    except ValueError:
        return REJECT_DAYS


def parallel_batches():
    """How many scoring calls one search makes at once (1-6, default 3)."""
    try:
        return max(1, min(6, int(os.environ.get("JH_SCORE_PARALLEL", "3"))))
    except ValueError:
        return 3


def profile_signature(profile_text, cv_size=0):
    """Everything the score depends on that the user can change."""
    raw = "%s|%s" % (profile_text or "", int(cv_size or 0))
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def recently_rejected(conn, user_id, sig, days=None):
    """URLs scored below the bar for this user and signature in the last `days`."""
    days = reject_days() if days is None else days
    if days <= 0:
        return set()
    rows = conn.execute(
        "SELECT url FROM score_skips WHERE user_id=? AND sig=? AND created_at >= ?",
        (user_id, sig, _stamp(days))).fetchall()
    return {r[0] for r in rows}


def remember_rejected(conn, user_id, sig, urls):
    """Record jobs the AI scored below the bar. Idempotent."""
    n = 0
    for url in urls:
        if not url:
            continue
        conn.execute("INSERT OR IGNORE INTO score_skips (user_id, url, sig, created_at) "
                     "VALUES (?,?,?,?)", (user_id, url, sig, _stamp()))
        n += 1
    conn.commit()
    return n


def prune(conn, days=None):
    """Drop rows older than the window; called once a day by the scheduler."""
    days = (reject_days() if days is None else days) or REJECT_DAYS
    conn.execute("DELETE FROM score_skips WHERE created_at < ?", (_stamp(days),))
    conn.commit()


def split_unscored(jobs, known_urls, rejected_urls):
    """(to_score, n_known, n_rejected). A job with no URL is always scored."""
    to_score, n_known, n_rejected = [], 0, 0
    for j in jobs:
        url = (j.get("url") or "").strip()
        if url and url in known_urls:
            n_known += 1
        elif url and url in rejected_urls:
            n_rejected += 1
        else:
            to_score.append(j)
    return to_score, n_known, n_rejected


class Unreliable(list):
    """A scorer answer that cannot tell rejected from lost: no JSON, or a
    truncated response salvaged object by object. Its misses are not remembered."""


def rejected_in(batch, kept):
    """URLs of a batch that the scorer did not keep."""
    kept_urls = {(k.get("url") or "").strip() for k in kept}
    return [u for u in ((b.get("url") or "").strip() for b in batch) if u and u not in kept_urls]


def run_batches(jobs, score_fn, size=20, parallel=None):
    """score_fn(batch) for each batch of `size`, `parallel` at a time.
    Results come back in batch order, so the output matches a sequential run."""
    batches = [jobs[i:i + size] for i in range(0, len(jobs), size)]
    if not batches:
        return []
    parallel = parallel_batches() if parallel is None else max(1, int(parallel))
    if parallel == 1 or len(batches) == 1:
        return [score_fn(b) for b in batches]
    with ThreadPoolExecutor(max_workers=min(parallel, len(batches)),
                            thread_name_prefix="score") as pool:
        return list(pool.map(score_fn, batches))
