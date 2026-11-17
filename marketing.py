"""marketing.py - consent to offers and news by email (GTM Stage 2, row 20).

Israeli law (the Communications Law's anti-spam section, "חוק הספאם") lets a
business send an advertisement by email only to someone who agreed to it
beforehand, in writing, and every such email must say "פרסומת" at the start of
its subject, name the sender with a way to reach them, and let the recipient
refuse further ones at no cost. So:

  * Sign-up has an UNTICKED box (WORDING). Ticking it is the consent; leaving
    it is a no. Google sign-ups from the sign-up page carry the same box.
  * Every change of answer is a row in marketing_consent (when, yes/no, where:
    'register' | 'google' | 'settings' | 'email', and the exact words shown), so
    the date and wording of a consent can be shown later. users.marketing_opt_in_at
    holds the current answer (NULL = no).
  * Settings has the same switch; every promotional email carries a one-click
    "stop offers" link (stop_url) and its subject goes through subject().
  * recipients() is the only list a promotional email may be sent to.

Service emails (password resets, Noa's picks, the check-ins in lifecycle.py)
are not advertisements and do not depend on this.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone

import lifecycle

WORDING = "Send me occasional offers and news from Job Hunter. You can stop at any time."
SOURCES = ("register", "google", "settings", "email")
AD_WORD = "פרסומת"
SENDER = "Job Hunter · support@job-hunter.work"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def record(conn, user_id: int, granted: bool, source: str, wording: str = WORDING) -> bool:
    """Store a yes or a no. Returns True if the answer changed. A repeat of the
    current answer adds no row, so the log is the list of real changes."""
    if source not in SOURCES:
        raise ValueError("unknown consent source: %r" % (source,))
    row = conn.execute("SELECT marketing_opt_in_at FROM users WHERE id=?", (int(user_id),)).fetchone()
    if row is None:
        return False
    was = bool(row[0])
    if was == bool(granted):
        return False
    at = _now()
    conn.execute("INSERT INTO marketing_consent (user_id, granted, source, wording, at) VALUES (?,?,?,?,?)",
                 (int(user_id), 1 if granted else 0, source, wording, at))
    conn.execute("UPDATE users SET marketing_opt_in_at=? WHERE id=?", (at if granted else None, int(user_id)))
    conn.commit()
    return True


def opted_in(conn, user_id: int) -> bool:
    row = conn.execute("SELECT marketing_opt_in_at FROM users WHERE id=?", (int(user_id),)).fetchone()
    return bool(row and row[0])


def recipients(conn) -> list:
    """(id, email, name) of everyone a promotional email may go to: said yes,
    account active and not being deleted."""
    return [tuple(r) for r in conn.execute(
        "SELECT id, email, name FROM users WHERE marketing_opt_in_at IS NOT NULL "
        "AND COALESCE(is_active, 1)=1 AND delete_after IS NULL ORDER BY id").fetchall()]


def subject(text: str) -> str:
    """The subject of a promotional email: the law wants the word first."""
    text = (text or "").strip()
    return text if text.startswith(AD_WORD) else "%s: %s" % (AD_WORD, text)


def stop_token(user_id: int) -> str:
    return hmac.new(lifecycle._key(), b"offers-off:%d" % int(user_id), hashlib.sha256).hexdigest()[:32]


def stop_ok(user_id, token) -> bool:
    try:
        return hmac.compare_digest(stop_token(int(user_id)), str(token or ""))
    except (TypeError, ValueError):
        return False


def stop_url(base: str, user_id: int) -> str:
    return "%s/email/offers-off?u=%d&t=%s" % (base.rstrip("/"), int(user_id), stop_token(user_id))


def footer(base: str, user_id: int) -> str:
    """The lines every promotional email ends with: who sent it, how to reach
    us, and how to stop."""
    return ("\n\n--\nYou're getting this because you asked for offers and news from Job Hunter.\n"
            "Stop these emails: %s\n%s" % (stop_url(base, user_id), SENDER))
