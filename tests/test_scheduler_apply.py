"""The scheduled apply fires once a day (Eran, 2026-11-08: "limit reached (20 out
of 20)" - it queued a run every minute of the apply hour, because it only
counted a day as done when an application was actually sent)."""
import pytest

import db as database
import auth
import jobqueue


@pytest.fixture
def env(tmp_path, monkeypatch):
    import app
    database.set_db_path(str(tmp_path / "t.db"))
    database.init_db()
    auth.set_db_getter(database.get_db)
    auth.create_user("Eran", "s@e.com", "pw123456")
    monkeypatch.delenv("APPLY_ENGINE_ENABLED", raising=False)
    conn = database.get_db()
    uid = conn.execute("SELECT id FROM users WHERE email=?", ("s@e.com",)).fetchone()["id"]
    hour = app.israel_now().hour
    conn.execute("UPDATE user_profiles SET apply_hour=?, search_hour=?, auto_apply_enabled=1, weekdays_only=0, "
                 "schedule_frequency='daily' WHERE user_id=?", (hour, (hour + 5) % 24, uid))
    conn.commit()
    conn.close()
    return app, uid


def _worker_runs_it(app, uid):
    conn = database.get_db()
    try:
        q = conn.execute("SELECT id FROM job_runs WHERE user_id=? AND kind='apply' AND status=?",
                         (uid, jobqueue.QUEUED)).fetchone()
        if q:
            app.run_job_apply(uid)            # engine off: returns at once, sends nothing
            conn.execute("UPDATE job_runs SET status=? WHERE id=?", (jobqueue.DONE, q["id"]))
            conn.commit()
    finally:
        conn.close()


def test_a_whole_apply_hour_starts_one_run_not_one_a_minute(env):
    app, uid = env
    for _ in range(25):                       # 25 scheduler ticks, a minute apart
        app._check_scheduled_jobs()
        _worker_runs_it(app, uid)
    assert jobqueue.runs_today(uid, "apply") == 1          # was 20, then the cap
    jobqueue.enqueue(uid, "apply")                          # the user's own press still works


def test_today_starts_at_israel_midnight_in_utc():
    import app
    assert app._israel_day_start_utc("2026-11-08") == "2026-11-07 22:00:00"   # winter, UTC+2
    assert app._israel_day_start_utc("2026-07-01") == "2026-06-30 21:00:00"   # summer, UTC+3
