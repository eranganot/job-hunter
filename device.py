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
import re
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


def applicant(user) -> dict:
    """The contact details every form asks for, from the Profile page (the
    source of truth for who is applying, 2026-11-07) - for the extension to fill
    where no prepared answer covers a field. No model call: the job list is
    asked for every time the popup opens."""
    import apply_engine as ae
    uid = user["id"]
    conn = database.get_db()
    try:
        p = conn.execute("SELECT phone, linkedin_url, locations FROM user_profiles WHERE user_id=?", (uid,)).fetchone()
        u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    finally:
        conn.close()
    u = dict(u) if u else {}
    name = (u.get("name") or user.get("name") or "").strip()
    parts = name.split()
    phone = ((p["phone"] if p else "") or u.get("phone") or "").strip()
    intl = ae.intl_phone(phone, "Israel") if phone else ""
    local = re.sub(r"^\+972[\s-]*", "0", intl) if intl.startswith("+972") else phone
    # user_profiles.locations is JSON text - '["Tel Aviv, Israel", "San Diego"]'.
    # 0.5.0 read it from users (no such column: always empty) and split it on
    # commas (the owner, 2026-11-23: "City is still missing"). The first place
    # that isn't remote/hybrid, its city part.
    city = ""
    try:
        locs = json.loads((p["locations"] if p else "") or "[]")
    except (TypeError, ValueError):
        locs = re.split(r"[;|]", (p["locations"] if p else "") or "")
    for c in (locs if isinstance(locs, list) else [str(locs)]):
        c = str(c).split(",")[0].strip()
        if c and not re.search(r"remote|hybrid|anywhere|^israel$", c, re.I):
            city = c
            break
    out = {"full_name": name, "first_name": parts[0] if parts else "",
           "last_name": " ".join(parts[1:]), "email": (u.get("email") or user.get("email") or "").strip(),
           "phone": intl, "phone_local": local, "linkedin_url": ((p["linkedin_url"] if p else "") or "").strip(),
           "city": city}
    try:
        import app as _app
        notes = _app._standing_answers(uid).get("standing_notes") or ""
        gh = re.search(r"https?://(?:www\.)?github\.com/[A-Za-z0-9_.-]+", notes)
        if gh:
            out["github_url"] = gh.group(0)
    except Exception as exc:  # noqa: BLE001 - GitHub is optional
        print("[device] standing notes for user %s: %s" % (uid, exc))
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


LINK_SYSTEMS = ("Comeet", "Lever", "SmartRecruiters")


def _page_meta(url: str) -> dict:
    """Title / company / description from the job page itself (og: tags, then
    <title>). Best effort: a page that refuses the server (SmartRecruiters'
    DataDome) leaves these empty and the link's own path names the company."""
    import html as _html
    import re
    import urllib.request
    out = {"title": "", "company": "", "description": ""}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (JobHunter link import)"})
        with urllib.request.urlopen(req, timeout=10) as r:
            page = r.read(600_000).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - any failure leaves the fields empty
        print(f"[device] page meta for {url[:80]}: {type(exc).__name__}")
        return out

    def meta(prop):
        m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=["\']([^"\']*)' % re.escape(prop), page, re.I) \
            or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']%s["\']' % re.escape(prop), page, re.I)
        return _html.unescape(m.group(1)).strip() if m else ""
    title = meta("og:title")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
        title = _html.unescape(m.group(1)).strip() if m else ""
    out["title"] = re.sub(r"\s+", " ", title)[:200]
    out["company"] = meta("og:site_name")[:120]
    out["description"] = meta("og:description")[:4000]
    return out


def _from_link(url: str) -> tuple:
    """(company, title) read from the link and the page: Lever "Company - Title",
    Comeet "Title at Company", SmartRecruiters' path."""
    import re
    from urllib.parse import urlparse, unquote
    path = [unquote(p) for p in urlparse(url).path.split("/") if p]
    slug_company = ""
    if "lever.co" in url and path:
        slug_company = path[0]
    elif "smartrecruiters.com" in url and path:
        slug_company = path[0]
    elif "comeet." in url and len(path) >= 2 and path[0] == "jobs":
        slug_company = path[1]
    m = _page_meta(url)
    title, company = m["title"], m["company"]
    if "lever.co" in url and " - " in title:
        company, title = title.split(" - ", 1)
    elif " at " in title and not company:
        title, company = title.rsplit(" at ", 1)
    if not company or company.lower() in ("lever", "comeet", "smartrecruiters"):
        company = re.sub(r"[-_]+", " ", slug_company).strip().title()
    return (company or "Unknown company")[:120], (title or "Job from a link")[:200], m["description"]


def add_by_link(user_id: int, url: str) -> dict:
    """B0 (the owner, 2026-11-22): a Comeet / Lever / SmartRecruiters job he
    wants to apply to, added to his queue as approved - his search had found
    no Lever or SmartRecruiters jobs to test with. Admin-only like the rest."""
    import apply_engine as ae
    url = (url or "").strip()
    if not url.lower().startswith("https://"):
        return {"ok": False, "error": "Paste the job's https:// link."}
    system = ae.ats_name(url)
    if system not in LINK_SYSTEMS:
        return {"ok": False, "error": "Only Comeet, Lever and SmartRecruiters links are part of this test (this one is %s)." % system}
    conn = database.get_db()
    try:
        old = conn.execute("SELECT id, title, company, status FROM jobs WHERE user_id=? AND url=?", (user_id, url)).fetchone()
        if old:
            if old["status"] not in ("approved", "applied"):
                conn.execute("UPDATE jobs SET status='approved' WHERE id=?", (old["id"],))
                conn.commit()
            return {"ok": True, "id": old["id"], "title": old["title"], "company": old["company"],
                    "system": system, "existing": True}
    finally:
        conn.close()
    company, title, desc = _from_link(url)
    import textclean
    j = textclean.clean_job({"title": title, "company": company, "description": desc})
    title, company, desc = j.get("title") or title, j.get("company") or company, j.get("description") or ""
    conn = database.get_db()
    try:
        conn.execute(
            "INSERT INTO jobs (user_id, title, company, location, url, description, why_relevant, source, "
            "found_date, status) VALUES (?,?,?,?,?,?,?,?,?, 'approved')",
            (user_id, title, company, "", url, desc, "Added by link (B0 test)", "link",
             datetime.now().isoformat()))
        jid = conn.execute("SELECT id FROM jobs WHERE user_id=? AND url=?", (user_id, url)).fetchone()["id"]
        conn.commit()
    finally:
        conn.close()
    database.log_activity(user_id, "job_added", "Added by link: %s at %s" % (title, company))
    return {"ok": True, "id": jid, "title": title, "company": company, "system": system, "existing": False}


def attempts(user_id: int) -> list:
    conn = database.get_db()
    try:
        rows = conn.execute(
            "SELECT a.*, j.title, j.company FROM device_attempts a LEFT JOIN jobs j ON j.id=a.job_id "
            "WHERE a.user_id=? ORDER BY a.id DESC LIMIT 200", (user_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]
