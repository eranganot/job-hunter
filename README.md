# 🎯 Job Hunter

An AI-powered personal job search assistant that runs entirely on **Railway** (cloud). It automatically finds relevant positions, lets you review and approve them, then applies on your behalf — and notifies you via WhatsApp every step of the way.

---

## Features

- **AI job search** — Claude scans job boards daily and surfaces roles matching your CV, job titles, and keywords
- **Smart scoring** — every job card shows a **match %** (how well the role fits your skills) and a **candidate score** (how strong a candidate you are)
- **Job status check** — one-click 🔍 button fetches the original posting and asks Claude if the role is still open
- **Review queue** — approve, pass, or defer jobs from a clean mobile-friendly dashboard
- **Auto-apply** — approved jobs are applied to automatically at your scheduled apply time
- **Run Search Now** — trigger an immediate AI job search from the New tab without waiting for the next scheduled run
- **Apply Now** — instantly apply to all approved jobs from the Approved tab, overriding the schedule
- **WhatsApp / Telegram notifications** — get notified when new jobs land, and when applications go out
- **Multi-user** — supports multiple accounts; the admin user's schedule drives all searches
- **Sort bar** — sort your job list by Date, Match score, or Company name
- **Bulk actions** — select multiple jobs and Approve All or Pass All in one click
- **Pass reason** — when passing on a job, optionally record why (Overqualified, Underqualified, Not interesting, Wrong location, Wrong salary, Already applied)
- **Activity log** — a dedicated Activity tab tracks all your actions: approvals, rejections, searches, CV uploads, and application updates
- **Admin panel** — admins can view all users, their pipeline stats, and activate/deactivate accounts at `/admin`

---

## Architecture

```
Railway (cloud)
  └── app.py              ← web app + REST API
       ├── /data/jobs.db  ← SQLite on persistent volume
       └── /data/uploads/ ← CVs on persistent volume

Mac (optional — for local search/apply automation)
  ├── search_jobs.py      ← Claude searches job boards, writes pending_jobs.json
  ├── apply_jobs.py       ← Claude fills in applications, writes applied_updates.json
  ├── relay.py            ← bridges local JSON files to Railway every 30 seconds
  └── config.json         ← RAILWAY_URL + SYNC_API_KEY + ANTHROPIC_API_KEY
```

Railway hosts the dashboard and persists all data. If you want fully automated searching and applying, the Mac-side scripts handle the heavy AI work and `relay.py` bridges between the two. The app is fully functional without the Mac side — you can manually trigger searches and manage jobs entirely from the dashboard.

---

## Tech Stack

- **Backend**: pure Python `http.server` (no Flask/Django) — zero extra dependencies
- **Database**: SQLite via Python stdlib `sqlite3`
- **Frontend**: vanilla JS + CSS (no framework, no build step)
- **AI**: Claude via the Anthropic API
- **Hosting**: Railway

---

## Quick Start (Local)

### 1. Configure

```bash
cp config.example.json config.json   # or edit config.json directly
```

Edit `config.json`:
```json
{
  "anthropic_api_key": "sk-ant-api03-...",
  "admin_email": "your@email.com",
  "sync_api_key": "generate-a-random-secret",
  "railway_url": "",
  "port": 5001
}
```

### 2. Run locally

```bash
python3 app.py
```

Open http://localhost:5001 → register with your admin email, upload your CV, and configure your preferences.

---

## Deploy to Railway

Full instructions are in [DEPLOY.md](DEPLOY.md). Quick summary:

1. Push this repo to GitHub
2. New Railway project → Deploy from GitHub repo
3. Add a persistent volume mounted at `/data`
4. Set environment variables:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your Anthropic key |
| `ADMIN_EMAIL` | your email |
| `SYNC_API_KEY` | same value as in `config.json` |
| `DATABASE_PATH` | `/data/jobs.db` |
| `UPLOADS_DIR` | `/data/uploads` |

5. Copy your Railway URL into `config.json` → `railway_url`

---

## Running the Mac-side tasks (optional)

### relay.py (always-on bridge)

```bash
python3 relay.py
```

Polls every 30 seconds → syncs pending jobs up to Railway, pulls approved jobs down for the apply task.

### Scheduled tasks

Set up two daily Claude Code tasks (or cron jobs):

| Task | File | Runs at |
|---|---|---|
| Job search | `search_jobs.py` | 11 AM (configurable) |
| Auto-apply | `apply_jobs.py` | 2 PM (configurable) |

Configure times in Settings → Schedule inside the web app.

---

## Sync API

All sync endpoints are authenticated with `SYNC_API_KEY` (passed as `?api_key=...`).

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/sync/jobs` | Ingest new jobs from the Mac search task |
| `GET` | `/api/sync/approved` | Fetch approved jobs for the Mac apply task |
| `POST` | `/api/sync/updates` | Mark jobs as applied/failed after apply runs |
| `POST` | `/api/sync/notify` | Deliver a WhatsApp/Telegram notification |

---

## Dashboard

| Tab | Contents |
|---|---|
| **New** | Freshly found jobs — Approve, Pass, or Later |
| **Approved** | Queued for auto-apply at scheduled time |
| **Applied** | Submitted applications with timestamp and notes |
| **Passed** | Jobs you skipped (with optional reason) |
| **Expired** | Old jobs past their window |
| **Activity** | Full history of all your actions |

### Sort bar

Every tab with jobs shows three sort options:

| Button | Sorts by |
|---|---|
| 📅 Date | Most recently found (default) |
| 🎯 Match | Highest match percentage first |
| 🏢 Company | Alphabetical by company name |


The **New** tab also shows a **🔍 Run Search Now** button in the sort bar to trigger an immediate AI search on demand. The **Approved** tab banner includes an **🚀 Apply Now** button to apply to all approved jobs instantly.

### Bulk actions

Click **Select** in the sort bar to enter selection mode. Check any jobs, then use the floating bar at the bottom to **Approve All** or **Pass All** in one action.

### Pass reason

When passing on a job, a modal prompts you to choose a reason:

- Overqualified
- Underqualified
- Not interesting
- Wrong location
- Wrong salary
- Already applied
- (Skip — no reason)

The reason is stored and shown in the Passed tab.

Each job card shows:
- **Match %** — keyword/title/seniority overlap with your profile (green ≥ 70%, amber ≥ 45%, red below)
- **Candidate score** — how competitive you are for the specific role
- **Status badge** — one-click 🔍 check if role is still open
- **Why this fits you** — AI explanation of relevance

---

## Admin Panel

Navigate to `/admin` (visible in the profile dropdown for admin accounts).

- View all registered users with their pipeline stats (New / Approved / Applied / Total jobs)
- See join date and active/inactive status
- Activate or deactivate non-admin accounts

---

## Activity Log

The Activity tab shows a chronological feed of everything that happened in your account:

- Jobs imported from a search run
- Individual approvals and passes (with reason if set)
- CV uploads and AI analysis runs
- Job status checks
- Application submissions and failures

---

## Project Structure

```
app.py              Main web server (routes, HTML, JS, auth, API)
ai_analysis.py      CV analysis, job scoring, status checking (Claude)
auth.py             Session-based auth (cookie + SQLite)
db.py               Database schema, migrations, and activity logging
relay.py            Mac <> Railway sync bridge (optional)
DEPLOY.md           Full Railway deployment guide
config.json         Local secrets (gitignored in production)
Procfile            Railway start command
```

---

## Database Schema

| Table | Purpose |
|---|---|
| `users` | Accounts, roles, preferences, schedule |
| `jobs` | All job postings with status, scores, notes |
| `rejected_patterns` | Passed jobs with optional reason |
| `activity_log` | Timestamped event log per user |

---

## License

MIT
