# 🎯 Job Hunter

An AI-powered personal job search assistant that runs on **Railway** (cloud) with scheduled tasks on your **Mac**. It automatically finds relevant positions, lets you review and approve them, then applies on your behalf — and notifies you via WhatsApp every step of the way.

---

## Features

- **AI job search** — Claude scans job boards daily and surfaces roles matching your CV, job titles, and keywords
- **Smart scoring** — every job card shows a **match %** (how well the role fits your skills) and a **candidate score** (how strong a candidate you are)
- **Job status check** — iYne-click 🔍 button fetches the original posting and asks Claude if the role is still open
- **Review queue** — approve, pass, or defer jobs from a clean mobile-friendly dashboard
- **Auto-apply** — approved jobs are applied to automatically at your scheduled apply time
- **WhatsApp / Telegram notifications** — get notified when new jobs land, and when applications go out
- **Multi-user** — supports multiple accounts; the admin user's schedule drives all searches

---

## Architecture

```
Mac (your computer)
  ├── search_jobs.py      ← Claude searches job boards, writes pending_jobs.json
  ├── apply_jobs.py        ← Claude fills in applications, writes applied_updates.json
  ├── relay.py             ← bridges local JSON files to Railway every 30 seconds
  └── config.json         ← RAILWAY_URL + SYNC_API_KEY + ANTHROPIC_API_KEY

Railway (cloud)
  └── app.py              ← web app + REST API
       ├── /data/jobs.db  ← SQLite on persistent volume
       └── /data/uploads/ ← CVs on persistent volume
```

The Mac does the heavy AI work (searching + applying). Railway hosts the dashboard and persists all data. `relay.py` is the bridge between the two.

---

## Tech Stack

- **Backend**: pure Python `http.server` (no Flask/Django) - zero extra dependencies
- **Database**: SQLite via Python stdlib `sqlite3`
- **AI**: Anthropic Claude (Haiku for status checks, Sonnet for search/apply)
- **Frontend**: vanilla JS + Tailwind CSS (served inline from `app.py`)
- **Hosting**: Railway with a persistent `/data` volume
- **Notifications**: Twilio (WhatsApp) or Telegram

---

## Local Setup

### Prerequisites
- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)

### 1. Clone and configure

```bash
git clone https://github.com/eranganot/job-hunter.git
cd job-hunter
cp config.json.example config.json   # or edit config.json directly
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

Open http://localhost:5001 — register with your admin email, upload your CV, and configure your preferences.

---

## Deploy to Railway

Full instructions are in [DEPLOY.md](DEPLOY.md). Quick summary:

1. Push this repo to GitHub
2. New Railway project ₒ Deploy from GitHub repo
3. Add a persistent volume mounted at `/data`
4. Set environment variables:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your Anthropic key |
| `ADMIN_EMAIL` | your email |
| `SYNC_API_KEY` | same value as in `config.json` |
| `DATABASE_PATH` | `/data/jobs.db` |
| `UPLOADS_DIR` | `/data/uploads` |

2. Copy your Railway URL into `config.json` → `railway_url`

---

## Running the Mac-side tasks

### relay.py (always-on bridge)

```bash
python3 relay.py
```

Polls every 30 seconds — syncs pending jobs up to Railway, pulls approved jobs down for the apply task.

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
| **Passed** | Jobs you skipped |
| **Expired** | Old jobs past their window |

Each job card shows:
- **Match %** — keyword/title/seniority overlap with your profile (green >= 70%, amber >= 45%, red below)
- **Candidate score** — how competitive you are for the specific role
- **Status badge** — iYne-click 🔍 check if role is still open
- **Why this fits you** — AI explanation of relevance

---

## Project Structure

```
app.py              Main web server (routes, HTML, JS, auth, API)
ai_analysis.py      CV analysis, job scoring, status checking (Claude)
auth.py             Session-based auth (cookie + SQLite)
db.py               Database schema and migrations
relay.py            Mac <> Railway sync bridge
DEPLOY.md           Full Railway deployment guide
config.json         Local secrets (gitignored in production)
Procfile            Railway process definition
runtime.txt         Python version pin
requirements.txt    Third-party dependencies (currently none)
```

---

## Security Notes

- `config.json` contains secrets — **do not commit it with real keys**. Set production values as Railway environment variables instead.
- The `SYNC_API_KEY` is a shared secret between `relay.py` and Railway — keep it out of version control.
- Session cookies are `HttpOnly` and tied to the SQLite `sessions` table.

---

## License

Personal use. Not open-sourced.
