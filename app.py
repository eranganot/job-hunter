#!/usr/bin/env python3
"""
Job Hunter – Standalone Multi-User App
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Local development:
    python3 app.py           → http://localhost:5001

Cloud (Railway):
    Set environment variables (see .env.example), push to GitHub, deploy.
    Run relay.py on your Mac to bridge scheduled tasks to the cloud.
"""

import json, os, re, shutil, threading, time
import urllib.parse, urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import auth
import db as database
from ai_analysis import analyze_cv

# Import configuration and utilities
from utils import (BASE_DIR, CONFIG, CONFIG_FILE, ANTHROPIC_KEY, ADMIN_EMAIL,
                   SYNC_API_KEY, PORT, DB_FILE, UPLOADS_DIR, LOCAL_IP, MOBILE_URL,
                   load_config, _cfg, get_local_ip, send_telegram, repair_mojibake,
                   send_whatsapp, deliver_notification, check_notifications,
                   _scheduler_already_ran, parse_multipart)
from templates import (error_block, LOGIN_HTML, REGISTER_HTML, ONBOARDING_HTML,
                       SETTINGS_HTML, ADMIN_HTML, DASHBOARD_HTML)
from search import run_job_search


# ── Scheduler ─────────────────────────────────────────────────────────────────

def _check_scheduled_jobs() -> None:
    """Auto-trigger search/apply for each active user when their scheduled hour arrives."""
    try:
        now = datetime.utcnow()
        today = now.strftime('%Y-%m-%d')
        current_hour = now.hour
        conn = database.get_db()
        rows = conn.execute(
            "SELECT u.id, p.search_hour, p.apply_hour, "
            "p.schedule_frequency, p.search_day_of_week, p.apply_day_of_week, p.weekdays_only "
            "FROM users u JOIN user_profiles p ON p.user_id = u.id "
            "WHERE u.is_active = 1"
        ).fetchall()
        conn.close()
        for row in rows:
            uid, sh, ah = row[0], row[1], row[2]
            freq = row[3] or 'daily'
            s_dow = row[4]
            a_dow = row[5]
            wo = row[6]
            cur_dow = now.weekday()  # 0=Mon ... 6=Sun
            # Skip weekends if weekdays_only
            if wo and cur_dow >= 5:
                continue
            # Search: check hour + frequency/day
            if current_hour == sh and not _scheduler_already_ran(uid, 'jobs_searched', today):
                run_search = True
                if freq == 'weekly' and s_dow is not None and cur_dow != s_dow:
                    run_search = False
                if run_search:
                    print(f'[scheduler] Triggering search for user {uid} at hour {sh}')
                    threading.Thread(target=run_job_search, args=(uid,), daemon=True).start()
            # Apply: check hour + frequency/day
            if current_hour == ah and not _scheduler_already_ran(uid, 'job_applied', today):
                run_apply = True
                if freq == 'weekly' and a_dow is not None and cur_dow != a_dow:
                    run_apply = False
                if run_apply:
                    print(f'[scheduler] Triggering apply for user {uid} at hour {ah}')
                    threading.Thread(target=run_job_apply, args=(uid,), daemon=True).start()
    except Exception as e:
        print(f'[scheduler] Error: {e}')


def file_watcher():
    while True:
        database.import_pending_jobs(BASE_DIR)
        database.import_applied_updates(BASE_DIR)
        check_notifications()
        _check_scheduled_jobs()
        time.sleep(60)


_search_running: set = set()




# ── Apply engine ──────────────────────────────────────────────────────────────

def run_job_apply(user_id: int) -> int:
    """Submit applications to all approved jobs using browser automation + Claude."""
    try:
        import apply_engine
        conn = database.get_db()
        jobs = conn.execute(
            "SELECT id, title, company, url FROM jobs WHERE user_id=? AND status='approved'",
            (user_id,)
        ).fetchall()

        if not jobs:
            conn.close()
            return {"applied": 0, "error": ""}

        # Gather user + CV data for form filling
        user    = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        profile = conn.execute(
            "SELECT cv_summary, cv_path FROM user_profiles WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()

        cv_text   = (profile["cv_summary"] or "") if profile else ""
        email     = user["email"] if user else ""
        applicant = apply_engine.extract_applicant_data(cv_text, email)

        cv_path = None
        if profile and profile["cv_path"]:
            cv_path = profile["cv_path"]

        today = datetime.now().strftime("%Y-%m-%d")
        count = 0
        confirmed_list, submitted_list, manual_list, failed_list = [], [], [], []

        for j in jobs:
            job_url = j["url"] or ""
            if job_url:
                res = apply_engine.submit_application(
                    job_url, j["title"], j["company"],
                    applicant, cv_path, ANTHROPIC_KEY
                )
                apply_status       = res["status"]
                apply_confirmation = res.get("confirmation_text", "")[:1000]
                apply_error        = res.get("error", "")[:500]
                notes = f"Applied via Job Hunter — {apply_status}"
            else:
                apply_status       = "submitted"
                apply_confirmation = ""
                apply_error        = "No URL available"
                notes = "Applied via Job Hunter (no URL)"

            c2 = database.get_db()
            c2.execute(
                "UPDATE jobs SET status='applied', applied_date=?, notes=?, "
                "apply_status=?, apply_confirmation=?, apply_error=?, "
                "apply_attempts=COALESCE(apply_attempts,0)+1 "
                "WHERE id=? AND user_id=?",
                (today, notes, apply_status, apply_confirmation,
                 apply_error, j["id"], user_id)
            )
            c2.commit()
            c2.close()

            database.log_activity(
                user_id, "job_applied",
                f"{j['title']} @ {j['company']} — {apply_status}"
            )
            count += 1
            if apply_status == "confirmed":
                confirmed_list.append(j)
            elif apply_status == "submitted":
                submitted_list.append(j)
            elif apply_status == "manual_required":
                manual_list.append(j)
            else:
                failed_list.append(j)

        # ── Notifications ────────────────────────────────────────────────────────────────────────────────
        # ── Single consolidated apply notification ──────────────────────────────
        today_str = datetime.now().strftime("%Y-%m-%d")
        notif_lines = [f"🚀 Apply Run Complete — {today_str}", f"📊 {count} application(s) submitted\n"]
        if confirmed_list:
            notif_lines.append(f"✅ {len(confirmed_list)} Confirmed:")
            for j in confirmed_list[:5]:
                notif_lines.append(f"  • {j['title']} @ {j['company']}")
            if len(confirmed_list) > 5:
                notif_lines.append(f"  … +{len(confirmed_list)-5} more")
        if submitted_list:
            notif_lines.append(f"\n📤 {len(submitted_list)} Submitted (awaiting confirmation):")
            for j in submitted_list[:5]:
                notif_lines.append(f"  • {j['title']} @ {j['company']}")
            if len(submitted_list) > 5:
                notif_lines.append(f"  … +{len(submitted_list)-5} more")
        if manual_list:
            notif_lines.append(f"\n👤 {len(manual_list)} Need Manual Apply:")
            for j in manual_list[:5]:
                notif_lines.append(f"  • {j['title']} @ {j['company']}")
            if len(manual_list) > 5:
                notif_lines.append(f"  … +{len(manual_list)-5} more")
        if failed_list:
            notif_lines.append(f"\n❌ {len(failed_list)} Failed:")
            for j in failed_list[:5]:
                notif_lines.append(f"  • {j['title']} @ {j['company']}")
            if len(failed_list) > 5:
                notif_lines.append(f"  … +{len(failed_list)-5} more")
        deliver_notification(user_id, "\n".join(notif_lines), url_suffix="/dashboard#applied")
        print(f"[run-apply] user {user_id}: {count} — confirmed={len(confirmed_list)} submitted={len(submitted_list)} manual={len(manual_list)} failed={len(failed_list)}")
        return {"applied": count, "error": ""}

    except Exception as e:
        print(f"[run-apply] Error: {e}")
        import traceback; traceback.print_exc()
        return {"applied": 0, "error": str(e)}


# ── Multipart parser ──────────────────────────────────────────────────────────



# ── HTTP Handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {fmt % args}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def send_html(self, html: str, code: int = 200):
        # Strip mojibake chars (U+0080-U+00FF: garbled box-drawing artifacts in templates)
        import re as _re_mj
        html = _re_mj.sub(r'[\x80-\xff]+', '', html)
        for _repair_pass in range(8):  # undo multiple layers of mojibake
            try:
                html = html.encode('latin-1').decode('utf-8', errors='strict')
            except Exception:
                break
        body = html.encode('utf-8')
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, code: int = 200):
        body = json.dumps(data, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def read_json(self) -> dict:
        try:
            return json.loads(self.read_body())
        except Exception:
            return {}

    def get_user(self):
        token = auth.get_token_from_request(self.headers)
        return auth.get_session_user(token)

    def require_auth(self):
        """Returns user dict or None (and sends redirect if not authed)."""
        user = self.get_user()
        if not user:
            self.redirect("/login")
        return user

    def _check_sync_key(self, key: str) -> bool:
        """Validate the shared secret used by relay.py and scheduled tasks."""
        return bool(SYNC_API_KEY) and key == SYNC_API_KEY

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── GET ───────────────────────────────────────────────────────────────────

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        qs     = parse_qs(parsed.query)

        # Public routes
        if path in ("/login", "/login/"):
            user = self.get_user()
            if user:
                self.redirect("/dashboard")
            else:
                self.send_html(LOGIN_HTML.replace("{error_block}", ""))
            return

        if path in ("/register", "/register/"):
            user = self.get_user()
            if user:
                self.redirect("/dashboard")
            else:
                self.send_html(REGISTER_HTML.replace("{error_block}", ""))
            return

        if path in ("/logout", "/logout/"):
            token = auth.get_token_from_request(self.headers)
            if token:
                auth.delete_session(token)
            self.send_response(302)
            self.send_header("Set-Cookie", auth.clear_session_cookie())
            self.send_header("Location", "/login")
            self.end_headers()
            return

        # Root redirect
        if path in ("/", ""):
            user = self.get_user()
            self.redirect("/dashboard" if user else "/login")
            return

        # Auth-required routes
        if path in ("/dashboard", "/dashboard/"):
            user = self.require_auth()
            if not user:
                return
            if not user.get("onboarding_complete"):
                self.redirect("/onboarding")
                return
            self.send_html(DASHBOARD_HTML)
            return

        if path in ("/onboarding", "/onboarding/"):
            user = self.require_auth()
            if not user:
                return
            self.send_html(ONBOARDING_HTML)
            return

        if path in ("/settings", "/settings/"):
            user = self.require_auth()
            if not user:
                return
            self.send_html(SETTINGS_HTML)
            return

        if path in ("/admin", "/admin/"):
            user = self.require_auth()
            if not user:
                return
            if user.get("role") != "admin":
                self.redirect("/dashboard")
                return
            self.send_html(ADMIN_HTML)
            return

        # Health check (no auth required)
        if path == "/api/health":
            import time as _ht
            conn = database.get_db()
            user_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
            job_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            last_search = conn.execute(
                "SELECT details, created_date FROM activity_log "
                "WHERE event_type='jobs_searched' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_apply = conn.execute(
                "SELECT details, created_date FROM activity_log "
                "WHERE event_type='job_applied' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            self.send_json({
                "status": "ok",
                "uptime_info": "server running",
                "active_users": user_count,
                "total_jobs": job_count,
                "last_search": {"detail": repair_mojibake(last_search[0]) if last_search else None, "date": last_search[1] if last_search else None},
                "last_apply": {"detail": repair_mojibake(last_apply[0]) if last_apply else None, "date": last_apply[1] if last_apply else None},
                "scheduler": "active (checks every 60s)",
            })
            return

        # API routes (all require auth)
        if path == "/api/me":
            user = self.require_auth()
            if not user:
                return
            self.send_json(user)
            return

        if path == "/api/stats":
            user = self.require_auth()
            if not user:
                return
            conn = database.get_db()
            self.send_json(database.get_stats(conn, user["id"]))
            conn.close()
            return

        if path == "/api/jobs":
            user = self.require_auth()
            if not user:
                return
            status  = qs.get("status", ["new"])[0]
            sort_by = qs.get("sort",   ["date"])[0]
            order_map = {
                "match":   "COALESCE(match_score, -1) DESC",
                "company": "company ASC",
                "date":    "found_date DESC",
            }
            order = order_map.get(sort_by, "found_date DESC")
            conn  = database.get_db()
            database.expire_old_jobs(conn, user["id"])
            if status == "all":
                rows = conn.execute(
                    f"SELECT * FROM jobs WHERE user_id=? ORDER BY {order}",
                    (user["id"],)
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT * FROM jobs WHERE user_id=? AND status=? ORDER BY {order}",
                    (user["id"], status)
                ).fetchall()
            jobs_list = [dict(r) for r in rows]

            # Auto-compute match/candidate scores for any unscored jobs
            try:
                from ai_analysis import compute_match_score, compute_candidate_score
                profile = conn.execute(
                    "SELECT * FROM user_profiles WHERE user_id=?", (user["id"],)
                ).fetchone()
                if profile and profile["cv_analyzed"]:
                    profile_dict = dict(profile)
                    updated = False
                    for job in jobs_list:
                        if job.get("match_score") is None:
                            ms = compute_match_score(job, profile_dict)
                            cs = compute_candidate_score(job, profile_dict)
                            conn.execute(
                                "UPDATE jobs SET match_score=?, candidate_score=? WHERE id=?",
                                (ms, cs, job["id"])
                            )
                            job["match_score"]     = ms
                            job["candidate_score"] = cs
                            updated = True
                    if updated:
                        conn.commit()
            except Exception as e:
                print(f"[score] Error computing scores: {e}")

            conn.close()
            self.send_json(jobs_list)
            return

        if path == "/api/activity":
            user = self.require_auth()
            if not user:
                return
            items = database.get_activity(user["id"], limit=100)
            for _it in items:
                if "details" in _it and _it["details"]:
                    _it["details"] = repair_mojibake(_it["details"])
            self.send_json(items)
            return

        if path == "/api/admin/dedup":
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "forbidden"}, status=403)
                return
            conn = database.get_db()
            # Find duplicate jobs: same user_id + company + title, keep the one with lowest id
            dupes = conn.execute("""
                SELECT j.id FROM jobs j
                INNER JOIN (
                    SELECT user_id, LOWER(TRIM(company)) as c, LOWER(TRIM(title)) as t, MIN(id) as min_id
                    FROM jobs
                    GROUP BY user_id, LOWER(TRIM(company)), LOWER(TRIM(title))
                    HAVING COUNT(*) > 1
                ) d ON j.user_id = d.user_id AND LOWER(TRIM(j.company)) = d.c AND LOWER(TRIM(j.title)) = d.t AND j.id != d.min_id
            """).fetchall()
            dupe_ids = [r[0] for r in dupes]
            if dupe_ids:
                conn.execute("DELETE FROM jobs WHERE id IN (%s)" % ",".join(str(i) for i in dupe_ids))
                conn.commit()
            conn.close()
            self.send_json({"removed": len(dupe_ids), "ids": dupe_ids})
            return

        if path == "/api/admin/users":
            user = self.require_auth()
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            conn = database.get_db()
            rows = conn.execute("""
                SELECT u.id, u.name, u.email, u.role, u.is_active, u.created_date,
                       (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id AND j.status='new')      AS stats_new,
                       (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id AND j.status='approved') AS stats_approved,
                       (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id AND j.status='applied')  AS stats_applied,
                       (SELECT COUNT(*) FROM jobs j WHERE j.user_id=u.id)                          AS stats_total
                FROM users u ORDER BY u.created_date DESC
            """).fetchall()
            conn.close()
            self.send_json([dict(r) for r in rows])
            return

        if path == "/api/patterns":
            user = self.require_auth()
            if not user:
                return
            conn = database.get_db()
            rows = conn.execute(
                "SELECT * FROM rejected_patterns WHERE user_id=? ORDER BY created_date DESC LIMIT 50",
                (user["id"],)
            ).fetchall()
            conn.close()
            self.send_json([dict(r) for r in rows])
            return

        # ── Sync: export approved jobs for relay/scheduled tasks ──
        if path == "/api/sync/approved":
            qs = parse_qs(parsed.query)
            if not self._check_sync_key(qs.get("api_key", [""])[0]):
                self.send_json({"error": "Unauthorized"}, 401)
                return
            conn = database.get_db()
            rows = conn.execute("SELECT * FROM jobs WHERE status='approved'").fetchall()
            conn.close()
            self.send_json([dict(r) for r in rows])
            return

        self.send_response(404)
        self.end_headers()

    # ── POST ──────────────────────────────────────────────────────────────────

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        # ── Login ──
        if path == "/login":
            body = urllib.parse.parse_qs(self.read_body().decode())
            email    = body.get("email", [""])[0]
            password = body.get("password", [""])[0]
            user, err = auth.authenticate(email, password)
            if err:
                html = LOGIN_HTML.replace("{error_block}", error_block(err))
                self.send_html(html)
                return
            token = auth.create_session(user["id"])
            self.send_response(302)
            self.send_header("Set-Cookie", auth.make_session_cookie(token))
            dest = "/dashboard" if user.get("onboarding_complete") else "/onboarding"
            self.send_header("Location", dest)
            self.end_headers()
            return

        # ── Register ──
        if path == "/register":
            body = urllib.parse.parse_qs(self.read_body().decode())
            name     = body.get("name", [""])[0].strip()
            email    = body.get("email", [""])[0]
            password = body.get("password", [""])[0]
            password2 = body.get("password2", [""])[0]
            if not name or not email or not password:
                html = REGISTER_HTML.replace("{error_block}", error_block("All fields are required."))
                self.send_html(html)
                return
            if password != password2:
                html = REGISTER_HTML.replace("{error_block}", error_block("Passwords don't match."))
                self.send_html(html)
                return
            if len(password) < 8:
                html = REGISTER_HTML.replace("{error_block}", error_block("Password must be at least 8 characters."))
                self.send_html(html)
                return
            user_id, err = auth.create_user(name, email, password)
            if err:
                html = REGISTER_HTML.replace("{error_block}", error_block(err))
                self.send_html(html)
                return
            token = auth.create_session(user_id)
            self.send_response(302)
            self.send_header("Set-Cookie", auth.make_session_cookie(token))
            self.send_header("Location", "/onboarding")
            self.end_headers()
            return

        # All API POST routes require auth
        user = self.get_user()
        if not user:
            self.send_json({"error": "Unauthorized"}, 401)
            return
        user_id = user["id"]

        # ── CV Upload ──
        if path == "/api/upload-cv":
            body = self.read_body()
            parts = parse_multipart(self.headers, body)
            cv_part = parts.get("cv")
            if not cv_part or not isinstance(cv_part, dict):
                self.send_json({"error": "No file received."})
                return
            if not cv_part["filename"].lower().endswith(".pdf"):
                self.send_json({"error": "Only PDF files are accepted."})
                return
            user_upload_dir = os.path.join(UPLOADS_DIR, str(user_id))
            os.makedirs(user_upload_dir, exist_ok=True)
            cv_path = os.path.join(user_upload_dir, "cv.pdf")
            with open(cv_path, "wb") as f:
                f.write(cv_part["data"])
            auth.update_profile(user_id, cv_path=cv_path, cv_analyzed=0)
            database.log_activity(user_id, "cv_uploaded", "Uploaded new CV PDF")
            print(f"[cv] Saved CV for user {user_id}: {cv_path}")
            self.send_json({"success": True, "path": cv_path})
            return

        # ── CV Analyze ──
        if path == "/api/analyze-cv":
            if not ANTHROPIC_KEY:
                self.send_json({"error": "Anthropic API key not configured. Add it to config.json."})
                return
            # Get CV path from profile
            conn = database.get_db()
            row = conn.execute("SELECT cv_path FROM user_profiles WHERE user_id=?", (user_id,)).fetchone()
            conn.close()
            cv_path = row["cv_path"] if row else None
            if not cv_path or not os.path.exists(cv_path):
                self.send_json({"error": "No CV uploaded yet. Please upload your PDF first."})
                return
            try:
                data = analyze_cv(cv_path, ANTHROPIC_KEY)
                # Save to profile
                auth.update_profile(
                    user_id,
                    cv_analyzed=1,
                    cv_summary=data.get("summary", ""),
                    job_titles=json.dumps(data.get("job_titles", [])),
                    keywords=json.dumps(data.get("keywords", [])),
                    locations=json.dumps(data.get("locations", ["Tel Aviv"])),
                    salary_min=data.get("salary_min", 0),
                    salary_max=data.get("salary_max", 0),
                    experience_years=data.get("experience_years", 0),
                    seniority=data.get("seniority", ""),
                )
                database.write_users_config(BASE_DIR)
                database.log_activity(user_id, "cv_analyzed",
                    f"AI extracted {len(data.get('job_titles',[]))} job titles, "
                    f"{len(data.get('keywords',[]))} keywords")
                self.send_json(data)
            except Exception as e:
                print(f"[analyze] Error: {e}")
                self.send_json({"error": str(e)})
            return

        # ── Save profile ──
        if path == "/api/save-profile":
            data = self.read_json()
            kwargs = {}
            for field in ("name", "phone", "linkedin_url"):
                if field in data:
                    kwargs[field] = data[field]
            if "name" in kwargs:
                auth.update_user(user_id, name=kwargs.pop("name"))
            for field in ("job_titles", "keywords", "locations"):
                if field in data:
                    val = data[field]
                    kwargs[field] = json.dumps(val) if isinstance(val, list) else val
            for field in ("salary_min", "salary_max"):
                if field in data:
                    kwargs[field] = int(data[field])
            if kwargs:
                auth.update_profile(user_id, **kwargs)
            database.write_users_config(BASE_DIR)
            self.send_json({"success": True})
            return

        # ── Save notifications ──
        if path == "/api/save-notifications":
            data = self.read_json()
            kwargs = {}
            for field in ("notification_channel", "telegram_token", "telegram_chat_id",
                          "twilio_account_sid", "twilio_auth_token", "whatsapp_number"):
                if field in data:
                    kwargs[field] = data[field]
            if kwargs:
                auth.update_profile(user_id, **kwargs)
            database.write_users_config(BASE_DIR)
            self.send_json({"success": True})
            return

        # ── Test notification ──
        if path == "/api/test-notification":
            data    = self.read_json()
            channel = data.get("channel", "none")
            msg     = f"✅ Job Hunter test message — connection works! Dashboard: {MOBILE_URL}"
            try:
                if channel == "telegram":
                    send_telegram(data.get("telegram_token",""), data.get("telegram_chat_id",""), msg)
                elif channel == "whatsapp":
                    send_whatsapp(data.get("twilio_account_sid",""), data.get("twilio_auth_token",""),
                                  data.get("whatsapp_number",""), msg)
                self.send_json({"success": True})
            except Exception as e:
                self.send_json({"success": False, "error": str(e)})
            return

        # ── Save schedule ──
        if path == "/api/save-schedule":
            data = self.read_json()
            kwargs = {}
            for int_field in ("search_hour", "apply_hour", "search_day_of_week",
                              "apply_day_of_week", "onboarding_complete", "weekdays_only"):
                if int_field in data:
                    kwargs[int_field] = int(data[int_field])
            if "schedule_frequency" in data:
                # Admin is always daily regardless of what was sent
                if user.get("role") == "admin":
                    kwargs["schedule_frequency"] = "daily"
                else:
                    kwargs["schedule_frequency"] = data["schedule_frequency"]
            if kwargs:
                auth.update_profile(user_id, **kwargs)
            database.write_users_config(BASE_DIR)
            self.send_json({"success": True})
            return

        # ── Change password ──
        if path == "/api/change-password":
            data = self.read_json()
            err = auth.change_password(user_id, data.get("current_password",""), data.get("new_password",""))
            if err:
                self.send_json({"success": False, "error": err})
            else:
                self.send_json({"success": True})
            return

        # ── Job actions ──
        m = re.match(r"^/api/jobs/(\d+)/(approve|reject|later|applied|failed|retry)$", path)
        if m:
            job_id = int(m.group(1))
            action = m.group(2)
            data   = self.read_json()
            conn   = database.get_db()

            # Verify ownership
            job = conn.execute(
                "SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)
            ).fetchone()
            if not job:
                conn.close()
                self.send_json({"error": "Not found"}, 404)
                return

            status_map = {"approve":"approved","reject":"rejected","later":"new","applied":"applied","failed":"failed","retry":"approved"}
            if action == "retry":
                conn.execute(
                    "UPDATE jobs SET status='approved', apply_status=NULL, "
                    "apply_error=NULL, apply_confirmation=NULL, "
                    "apply_attempts=0, applied_date=NULL, notes='' "
                    "WHERE id=? AND user_id=?",
                    (job_id, user_id)
                )
                conn.commit()
                conn.close()
                database.log_activity(user_id, "job_retry",
                    f"Retrying {job['title']} at {job['company']}")
                self.send_json({"success": True})
                return

            new_status = status_map[action]
            reason     = data.get("reason", "") or data.get("notes", "")

            if action == "later":
                conn.execute("UPDATE jobs SET found_date=?, notes=? WHERE id=?",
                             (datetime.now().isoformat(), reason, job_id))
            elif action in ("applied", "failed"):
                conn.execute("UPDATE jobs SET status=?, applied_date=?, notes=? WHERE id=?",
                             (new_status, datetime.now().isoformat(), reason, job_id))
            else:
                conn.execute("UPDATE jobs SET status=?, notes=? WHERE id=?",
                             (new_status, reason, job_id))

            if action == "reject":
                conn.execute(
                    "INSERT INTO rejected_patterns (user_id,company,title,notes,created_date) VALUES (?,?,?,?,?)",
                    (user_id, job["company"], job["title"],
                     reason or "No reason given", datetime.now().isoformat())
                )
                detail = f"Passed on {job['title']} at {job['company']}"
                if reason:
                    detail += f" — {reason}"
                database.log_activity(user_id, "job_rejected", detail)
            elif action == "approve":
                database.log_activity(user_id, "job_approved",
                    f"Approved {job['title']} at {job['company']}")

            conn.commit()
            conn.close()
            database.write_approved_jobs(BASE_DIR)
            self.send_json({"success": True})
            return

        # ── Check if job is still open (calls Claude + fetches URL) ──────────
        m = re.match(r"^/api/jobs/(\d+)/check-status$", path)
        if m:
            user = self.require_auth()
            if not user:
                return
            job_id = int(m.group(1))
            conn = database.get_db()
            job = conn.execute(
                "SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user["id"])
            ).fetchone()
            if not job:
                conn.close()
                self.send_json({"error": "Job not found"}, 404)
                return
            if not job["url"]:
                conn.close()
                self.send_json({"error": "No URL for this job"}, 400)
                return
            try:
                from ai_analysis import check_job_status
                result = check_job_status(
                    job["url"], job["title"], job["company"], ANTHROPIC_KEY
                )
                status_str = result.get("status_check", "unknown")
                conn.execute(
                    "UPDATE jobs SET status_check=?, status_checked_date=? WHERE id=?",
                    (status_str, datetime.now().isoformat(), job_id)
                )
                conn.commit()
                database.log_activity(user["id"], "job_status_checked",
                    f"{job['title']} at {job['company']} — {status_str}")
            except Exception as e:
                result = {"error": str(e), "status_check": "unknown", "reason": str(e)}
            conn.close()
            self.send_json(result)
            return

        # ── Update applied-job pipeline stage ───────────────────────────────────
        if path == "/api/set-stage":
            user = self.get_user()
            if not user:
                self.send_json({"error": "auth"}, 401); return
            data   = self.read_json()
            job_id = data.get("id")
            stage  = data.get("stage")
            if not job_id or stage not in ("screening","interviewing","offer","rejected"):
                self.send_json({"error": "invalid"}, 400); return
            conn = database.get_db()
            conn.execute(
                "UPDATE jobs SET apply_status=? WHERE id=? AND user_id=?",
                (stage, job_id, user["id"])
            )
            conn.commit(); conn.close()
            database.log_activity(user["id"], "stage_update",
                f"Stage updated to {stage} for job {job_id}")
            self.send_json({"ok": True}); return

        # ── Bulk job actions ──────────────────────────────────────────────────────
        if path == "/api/jobs/bulk":
            data   = self.read_json()
            action = data.get("action", "")
            ids    = [int(i) for i in data.get("ids", []) if str(i).isdigit()]
            if not ids or action not in ("approve", "reject"):
                self.send_json({"error": "Invalid"}, 400)
                return
            conn       = database.get_db()
            new_status = "approved" if action == "approve" else "rejected"
            done       = 0
            for job_id in ids:
                job = conn.execute(
                    "SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)
                ).fetchone()
                if not job:
                    continue
                conn.execute("UPDATE jobs SET status=? WHERE id=?", (new_status, job_id))
                if action == "reject":
                    conn.execute(
                        "INSERT INTO rejected_patterns (user_id,company,title,notes,created_date) VALUES (?,?,?,?,?)",
                        (user_id, job["company"], job["title"], "Bulk pass", datetime.now().isoformat())
                    )
                done += 1
            conn.commit()
            conn.close()
            label = "Approved" if action == "approve" else "Passed on"
            database.log_activity(user_id, f"bulk_{action}", f"{label} {done} job(s) at once")
            database.write_approved_jobs(BASE_DIR)
            self.send_json({"success": True, "updated": done})
            return

        # ── Admin: toggle user active state ───────────────────────────────────
        m = re.match(r"^/api/admin/users/(\d+)/toggle$", path)
        if m:
            if user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            target_id = int(m.group(1))
            conn = database.get_db()
            conn.execute("UPDATE users SET is_active = 1 - is_active WHERE id=?", (target_id,))
            conn.commit()
            conn.close()
            self.send_json({"success": True})
            return

        # ── Run Search Now ────────────────────────────────────────────────────────
        if path == "/api/run-search":
            if not user:
                self.send_json({"error": "Unauthorized"}, 401)
                return
            if not ANTHROPIC_KEY:
                self.send_json({"error": "Anthropic API key not configured"}, 400)
                return
            uid = user["id"]
            threading.Thread(target=run_job_search, args=(uid,), daemon=True).start()
            self.send_json({"status": "started"})
            return

        # ── Run Apply Now ─────────────────────────────────────────────────────────
        if path == "/api/run-apply":
            if not user:
                self.send_json({"error": "Unauthorized"}, 401)
                return
            result = run_job_apply(user["id"])
            self.send_json(result)
            return

        # ── Admin job inject — session-authenticated, admin only ────────────────
        if path == "/api/admin/inject-jobs":
            if not user or user.get("role") != "admin":
                self.send_json({"error": "Forbidden"}, 403)
                return
            payload = self.read_json()
            jobs = payload.get("jobs", [])
            conn = database.get_db()
            inserted = 0
            for j in jobs:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO jobs "
                        "(user_id,title,company,location,url,description,why_relevant,source,"
                        "found_date,match_score,candidate_score,status) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,'new')",
                        (user["id"], j.get("job_title",""), j.get("company",""),
                         j.get("location",""), j.get("url",""), j.get("description",""),
                         j.get("fit_reason",""), j.get("source",""), j.get("found_date",""),
                         j.get("match_score",0), j.get("candidate_score",0)))
                    inserted += 1
                except Exception as e:
                    print(f"[inject] {e}")
            conn.commit()
            conn.close()
            database.log_activity(user["id"], "jobs_injected",
                                  f"{inserted} jobs added via admin inject")
            self.send_json({"inserted": inserted})
            return

        # ── Sync endpoints — called by relay.py on Mac, no session needed ──────

        if path == "/api/sync/jobs":
            data = self.read_json()
            if not self._check_sync_key(data.get("api_key", "")):
                self.send_json({"error": "Unauthorized"}, 401)
                return
            jobs = data.get("jobs", [])
            conn = database.get_db()
            inserted = 0
            for j in jobs:
                uid = j.get("user_id", 1)
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO jobs
                          (user_id,title,company,location,url,description,
                           why_relevant,company_info,source,found_date,status)
                        VALUES (?,?,?,?,?,?,?,?,?,?,'new')
                    """, (uid, j.get("title",""), j.get("company",""),
                          j.get("location","Tel Aviv"), j.get("url",""),
                          j.get("description",""), j.get("why_relevant",""),
                          j.get("company_info",""), j.get("source",""),
                          j.get("found_date", datetime.now().isoformat())))
                    if conn.execute("SELECT changes()").fetchone()[0] > 0:
                        inserted += 1
                except Exception:
                    pass
            conn.commit()
            conn.close()
            if inserted > 0:
                for uid in set(j.get("user_id", 1) for j in jobs):
                    cnt = sum(1 for j in jobs if j.get("user_id", 1) == uid)
                    database.log_activity(uid, "jobs_searched", f"Relay synced {cnt} new job(s)")
            print(f"[sync] {inserted} new jobs ingested via API")
            self.send_json({"success": True, "inserted": inserted})
            return

        if path == "/api/sync/updates":
            data = self.read_json()
            if not self._check_sync_key(data.get("api_key", "")):
                self.send_json({"error": "Unauthorized"}, 401)
                return
            updates = data.get("updates", [])
            conn = database.get_db()
            for u in updates:
                conn.execute(
                    "UPDATE jobs SET status=?, applied_date=?, notes=? WHERE id=?",
                    (u.get("status","applied"), u.get("applied_date"),
                     u.get("notes",""), u["id"])
                )
            conn.commit()
            conn.close()
            if updates:
                # Log activity per user — look up user_id for each updated job
                conn2 = database.get_db()
                uid_counts: dict = {}
                for u in updates:
                    row = conn2.execute("SELECT user_id FROM jobs WHERE id=?", (u["id"],)).fetchone()
                    if row:
                        uid_counts[row["user_id"]] = uid_counts.get(row["user_id"], 0) + 1
                conn2.close()
                for uid, cnt in uid_counts.items():
                    database.log_activity(uid, "job_applied", f"Auto-applied to {cnt} job(s)")
            print(f"[sync] {len(updates)} job statuses updated via API")
            self.send_json({"success": True, "updated": len(updates)})
            return

        if path == "/api/sync/notify":
            data = self.read_json()
            if not self._check_sync_key(data.get("api_key", "")):
                self.send_json({"error": "Unauthorized"}, 401)
                return
            message = data.get("message", "Job Hunter notification")
            user_id = int(data.get("user_id", 1))
            deliver_notification(user_id, message)
            self.send_json({"success": True})
            return

        self.send_response(404)
        self.end_headers()

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n🎯  Job Hunter (Multi-User) starting…")
    database.init_db()

    # Process any waiting files
    database.import_pending_jobs(BASE_DIR)
    database.import_applied_updates(BASE_DIR)
    check_notifications()

    # Background watcher
    t = threading.Thread(target=file_watcher, daemon=True)
    t.start()

    ai_status = "✅ Configured" if ANTHROPIC_KEY else "⚠️  Not set — add to config.json"

    print(f"\n📂  Folder:        {BASE_DIR}")
    print(f"🗄️   Database:      jobs.db")
    print(f"🤖  Anthropic AI:  {ai_status}")
    print(f"\n🖥️   Desktop:       http://localhost:{PORT}")
    print(f"📱  Mobile:        {MOBILE_URL}   ← open on your phone")
    print(f"⌨️   Ctrl+C to stop\n")

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋  Stopped.")

