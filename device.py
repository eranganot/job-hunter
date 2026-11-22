"""
Plan B, test B0 (2026-11-22): applications sent from the owner's own device.

The server keeps preparing (the filled-in answers C already stores in
jobs.apply_prepared, the CV); a Chrome extension or a WebView test app on the
owner's device fills the employer's form, presses Submit from the owner's own
connection and reports what the site answered. Comeet, Lever and SmartRecruiters
refuse the server's browser (Comeet 423 every time since 2026-11-10, DataDome,
Lever's puzzle) but took the owner's own application by hand - B0 measures
whether they take one sent from his device.

Owner-only, like auto-apply (the owner, 2026-11-22): every token belongs to an
admin account and stops working if the account is no longer admin.

Auth is a bearer token, never the session cookie, so these calls cannot be
made by another site through the owner's browser: the cross-site check that
guards cookie requests does not apply to them.
"""
import hashlib
import json
import random
import secrets
from datetime import datetime, timedelta, timezone

import db as database

CODE_MINUTES = 10
OUTCOMES = ("sent", "blocked", "nothing_sent", "unknown")
PRESSED_BY = ("user", "extension")


def _h(s: str) -> str:
    return hashlib.sha256((s or "").encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def new_code(user_id: int) -> str:
    """A 6-digit pairing code for this user, valid CODE_MINUTES. Replaces any
    code the user had."""
    code = "%06d" % random.SystemRandom().randrange(0, 1_000_000)
    exp = (datetime.now(timezone.utc) + timedelta(minutes=CODE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    conn = database.get_db()
    try:
        conn.execute("DELETE FROM device_codes WHERE user_id=? OR expires_at < ?", (user_id, _now()))
        conn.execute("INSERT INTO device_codes (code_hash, user_id, expires_at) VALUES (?,?,?)",
                     (_h(code), user_id, exp))
        conn.commit()
    finally:
        conn.close()
    return code


def pair(code: str, label: str = "") -> str | None:
    """Swap a valid code for a device token (shown once, stored hashed).
    None when the code is wrong, used or expired."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != 6:
        return None
    conn = database.get_db()
    try:
        r = conn.execute("SELECT user_id FROM device_codes WHERE code_hash=? AND expires_at >= ?",
                         (_h(code), _now())).fetchone()
        if not r:
            return None
        conn.execute("DELETE FROM device_codes WHERE code_hash=?", (_h(code),))
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO device_tokens (user_id, token_hash, label, created_at) VALUES (?,?,?,?)",
                     (r["user_id"], _h(token), (label or "device")[:60], _now()))
        conn.commit()
        return token
    finally:
        conn.close()


def user_for(headers) -> dict | None:
    """The admin account behind 'Authorization: Bearer <device token>', or None."""
    auth = (headers.get("Authorization", "") if headers else "") or ""
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    if not token:
        return None
    conn = database.get_db()
    try:
        r = conn.execute(
            "SELECT u.*, t.id AS device_token_id FROM device_tokens t JOIN users u ON u.id=t.user_id "
            "WHERE t.token_hash=? AND COALESCE(t.revoked,0)=0", (_h(token),)).fetchone()
        if not r or (r["role"] or "").strip().lower() != "admin":
            return None
        conn.execute("UPDATE device_tokens SET last_used_at=? WHERE id=?", (_now(), r["device_token_id"]))
        conn.commit()
        return dict(r)
    finally:
        conn.close()


def jobs(user_id: int) -> list:
    """The user's approved jobs, with what the server prepared for each."""
    import apply_engine as ae
    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT id, title, company, url, apply_status, apply_error, apply_failure_type, apply_prepared "
            "FROM jobs WHERE user_id=? AND status='approved' ORDER BY id DESC", (user_id,)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        try:
            prepared = json.loads(r["apply_prepared"]) if r["apply_prepared"] else []
        except (TypeError, ValueError):
            prepared = []
        url = r["url"] or ""
        out.append({"id": r["id"], "title": r["title"], "company": r["company"], "url": url,
                    "system": ae.ats_name(url), "route": ae.apply_route(url),
                    "apply_status": r["apply_status"] or "", "apply_error": r["apply_error"] or "",
                    "prepared": prepared if isinstance(prepared, list) else []})
    return out


def record(user_id: int, data: dict) -> dict:
    """One attempt from a device. 'sent' also moves the job to applied, sent by
    the device (applied_via='device': the success-rate table counts it as sent,
    never as a hand-marked job). Other outcomes leave the job where it is."""
    outcome = str(data.get("outcome") or "unknown")
    if outcome not in OUTCOMES:
        outcome = "unknown"
    pressed = str(data.get("pressed_by") or "")
    pressed = pressed if pressed in PRESSED_BY else "user"
    try:
        job_id = int(data.get("job_id"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "job_id is required"}
    conn = database.get_db()
    try:
        job = conn.execute("SELECT id, url FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
        if not job:
            return {"ok": False, "error": "job not found"}
        import apply_engine as ae
        conn.execute(
            "INSERT INTO device_attempts (user_id, job_id, system, device, pressed_by, outcome, site_status, "
            "detail, url, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user_id, job_id, ae.ats_name(job["url"] or ""), str(data.get("device") or "")[:40], pressed,
             outcome, str(data.get("site_status") or "")[:40], str(data.get("detail") or "")[:500],
             str(data.get("url") or "")[:500], _now()))
        if outcome == "sent":
            now = datetime.now().isoformat()
            conn.execute(
                "UPDATE jobs SET status='applied', applied_date=?, apply_status=?, applied_via='device', "
                "apply_submitted_at=?, apply_error=NULL, apply_failure_type=NULL, apply_failure_detail=NULL, "
                "apply_confirmation=? WHERE id=? AND user_id=?",
                (now, "confirmed" if data.get("confirmed") else "submitted", now,
                 str(data.get("detail") or "Sent from the device")[:400], job_id, user_id))
        conn.commit()
    finally:
        conn.close()
    if outcome == "sent":
        database.log_activity(user_id, "job_applied", "Sent from %s (%s pressed Submit): job %s"
                              % (str(data.get("device") or "device")[:40], pressed, job_id))
    return {"ok": True}


def attempts(user_id: int) -> list:
    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT a.*, j.title, j.company FROM device_attempts a LEFT JOIN jobs j ON j.id=a.job_id "
            "WHERE a.user_id=? ORDER BY a.id DESC LIMIT 200", (user_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
