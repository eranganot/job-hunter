"""Lifecycle emails (launch Stage 2 item 7, the owner 2026-11-12): welcome, day 1,
day 3 (from Eran), day 7 - templated, personalised, reacting to what the user
did; 11:30 Israel time; only new signups; never twice, never late."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_routes import Client, stack, users  # noqa: F401

import lifecycle

TZ = lifecycle._tz()


def _il(y, m, d, h, mi):
    return datetime(y, m, d, h, mi, tzinfo=TZ).astimezone(timezone.utc)


def test_the_schedule():
    created = _il(2026, 11, 20, 9, 0)
    assert lifecycle.due_step(created, created + timedelta(minutes=30), set()) is None
    assert lifecycle.due_step(created, created + timedelta(minutes=61), set()) == ("welcome", "send")
    assert lifecycle.due_step(created, created + timedelta(hours=25), set()) == ("welcome", "skip")
    done = {"welcome"}
    assert lifecycle.due_step(created, _il(2026, 11, 21, 11, 29), done) is None
    assert lifecycle.due_step(created, _il(2026, 11, 21, 11, 30), done) == ("day1", "send")
    assert lifecycle.due_step(created, _il(2026, 11, 22, 8, 0), done) == ("day1", "skip")      # never late
    done |= {"day1"}
    assert lifecycle.due_step(created, _il(2026, 11, 23, 12, 0), done) == ("day3", "send")
    done |= {"day3"}
    assert lifecycle.due_step(created, _il(2026, 11, 26, 23, 0), done) is None
    assert lifecycle.due_step(created, _il(2026, 11, 27, 11, 45), done) == ("day7", "send")
    assert lifecycle.due_step(created, _il(2026, 12, 1, 11, 45), done | {"day7"}) is None


def test_every_variant_is_personal_and_signed():
    for p in lifecycle.previews("Maya", "https://job-hunter.work/app"):
        assert p["body"].startswith("Hi Maya,"), p["subject"]
        assert "Stop them here" in p["body"]
        assert ("Your personal AI headhunter agent at Job Hunter,\nNoa" in p["body"]) == (p["sender"] == "noa")
        assert ("Eran Ganot\nThe human behind Noa" in p["body"]) == (p["sender"] == "eran")
        assert "beta" not in (p["subject"] + p["body"]).lower()
    assert [p["sender"] for p in lifecycle.previews("", "x") if p["step"] == "day3"] == ["eran", "eran"]
    assert lifecycle.previews("", "x")[0]["body"].startswith("Hi,\n")          # no name, no "Hi ,"


def test_the_unsubscribe_token():
    t = lifecycle.unsubscribe_token(7)
    assert lifecycle.unsubscribe_ok(7, t) and not lifecycle.unsubscribe_ok(8, t)
    assert not lifecycle.unsubscribe_ok("x", t) and not lifecycle.unsubscribe_ok(7, "")


@pytest.fixture
def life(stack):
    db = stack["db"].get_db()
    db.execute("DELETE FROM app_flags WHERE key='lifecycle_since'")
    db.execute("INSERT INTO app_flags (key, value) VALUES ('lifecycle_since', '2026-11-20 00:00:00')")
    db.commit(); db.close()
    return stack


def _signup(stack, email, created, name="Maya Cohen", setup=False):
    import ratelimit
    ratelimit.reset_all()
    Client(stack["port"]).post_form("/register", {"name": name, "email": email,
                                                  "password": "pw-long-enough-1", "password2": "pw-long-enough-1"})
    db = stack["db"].get_db()
    uid = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
    db.execute("UPDATE users SET created_date=? WHERE id=?", (created.strftime("%Y-%m-%d %H:%M:%S"), uid))
    db.execute("UPDATE user_profiles SET onboarding_complete=? WHERE user_id=?", (1 if setup else 0, uid))
    db.commit(); db.close()
    return uid


def _run(stack, now, senders=None):
    sent = []
    out = lifecycle.run(stack["db"].get_db, lambda to, subj, body, who, unsub: sent.append((to, subj, body, who)),
                        "https://job-hunter.work", now=now, senders=senders)
    return out, sent


def test_the_sweep_sends_once_reacts_and_respects_stop(life):
    t0 = _il(2026, 11, 20, 9, 0)
    uid = _signup(life, "lc-1@example.test", t0)
    _signup(life, "lc-old@example.test", _il(2026, 11, 19, 9, 0))           # before the feature: never
    out, sent = _run(life, t0 + timedelta(minutes=70))
    assert [(s[0], s[3]) for s in sent] == [("lc-1@example.test", "noa")]
    assert sent[0][1] == "Maya, I can't start without you"
    assert _run(life, t0 + timedelta(minutes=80))[1] == []                     # once
    db = life["db"].get_db()
    db.execute("UPDATE user_profiles SET onboarding_complete=1 WHERE user_id=?", (uid,))
    db.execute("INSERT INTO jobs (user_id,title,company,url,status,match_score,released_on) "
               "VALUES (?,?,?,?, 'new', 80, '2026-11-21')", (uid, "Data Analyst", "Orbitly", "https://x.test/1"))
    db.commit(); db.close()
    _o, sent = _run(life, _il(2026, 11, 21, 11, 31))
    assert sent[0][1] == "1 job I picked for you is waiting" and "Data Analyst at Orbitly" in sent[0][2]
    # day 3 needs Eran's sender; without it the step is skipped, not sent from someone else
    _o, sent = _run(life, _il(2026, 11, 23, 11, 31), senders={"noa": True, "eran": False})
    assert sent == []
    db = life["db"].get_db()
    assert db.execute("SELECT variant FROM lifecycle_emails WHERE user_id=? AND step='day3'", (uid,)).fetchone()[0] == "no_sender"
    db.execute("UPDATE users SET lifecycle_opt_out=1 WHERE id=?", (uid,)); db.commit(); db.close()
    assert _run(life, _il(2026, 11, 27, 11, 31))[1] == []


def test_the_stop_link(life):
    uid = _signup(life, "lc-2@example.test", _il(2026, 11, 20, 9, 0))
    c = Client(life["port"])
    t = lifecycle.unsubscribe_token(uid)
    st, _l, body = c.get("/email/stop?u=%d&t=%s" % (uid, t))
    assert st == 200 and b"Stop these emails?" in body
    assert c.get("/email/stop?u=%d&t=bad" % uid)[0] == 400
    db = life["db"].get_db()
    assert not db.execute("SELECT lifecycle_opt_out FROM users WHERE id=?", (uid,)).fetchone()[0]   # GET changes nothing
    db.close()
    assert c.post_form("/email/stop?u=%d&t=%s" % (uid, t), {"List-Unsubscribe": "One-Click"})[0] == 200
    db = life["db"].get_db()
    assert db.execute("SELECT lifecycle_opt_out FROM users WHERE id=?", (uid,)).fetchone()[0] == 1
    db.close()


def test_the_admin_can_send_himself_every_variant(stack, users, monkeypatch):
    app = stack["app"]
    got = []
    monkeypatch.setattr(app, "send_email", lambda to, subj, body, *a, **k: got.append((to, subj, k.get("sender"))))
    assert users["a"].post_json("/api/admin/lifecycle-test", {})[0] == 403
    out = json.loads(users["admin"].post_json("/api/admin/lifecycle-test", {})[2])
    assert len(out["sent"]) == len(lifecycle.SAMPLES) and not out["errors"]
    assert {g[0] for g in got} == {"admin@example.test"}
    assert [g[2] for g in got if g[1].startswith("[day3")] == ["eran", "eran"]


def test_off_unless_switched_on(monkeypatch):
    monkeypatch.delenv("LIFECYCLE_EMAILS", raising=False)
    assert not lifecycle.enabled()
    monkeypatch.setenv("LIFECYCLE_EMAILS", "1")
    assert lifecycle.enabled()
