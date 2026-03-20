# Job Hunter — Railway Deployment Guide

## Overview

```
Mac (your computer)
  ├── scheduled tasks (search_jobs.py, apply_jobs.py)
  ├── relay.py  ← new: pushes/pulls JSON to/from Railway
  └── config.json  (RAILWAY_URL + SYNC_API_KEY)

Railway (cloud)
  └── app.py  ← the web app + sync API endpoints
       └── /data/jobs.db  (persistent volume)
       └── /data/uploads/ (persistent volume)
```

---

## Step 1 — Push to GitHub

1. Open Terminal in your `job-hunter/` folder.
2. Initialise git (if you haven't already):
   ```bash
   cd ~/path/to/job-hunter
   git init
   git add .
   git commit -m "Initial commit"
   ```
3. Create a new **private** repo on github.com, then push:
   ```bash
   git remote add origin https://github.com/YOUR_USERNAME/job-hunter.git
   git branch -M main
   git push -u origin main
   ```

---

## Step 2 — Create a Railway project

1. Go to **railway.app** → New Project → Deploy from GitHub repo.
2. Select your `job-hunter` repo.
3. Railway detects `Procfile` automatically and starts a build.

---

## Step 3 — Attach a Persistent Volume

SQLite and uploaded CVs must survive deploys — Railway's ephemeral disk resets on every deploy.

1. In your Railway project, click your service → **Settings** → **Volumes**.
2. Click **Add Volume** → mount path: `/data` → click **Add**.
3. Railway will redeploy automatically once the volume is attached.

---

## Step 4 — Set Environment Variables

In your Railway service → **Variables**, add:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-api03-…` (your real key) |
| `ADMIN_EMAIL` | `eran.ganot@gmail.com` |
| `SYNC_API_KEY` | same value as `sync_api_key` in your local `config.json` |
| `DATABASE_PATH` | `/data/jobs.db` |
| `UPLOADS_DIR` | `/data/uploads` |

> **PORT** is injected by Railway automatically — do not set it manually.

Click **Deploy** after saving variables.

---

## Step 5 — Get your Railway URL

1. In Railway ₒ your service → **Settings** → **Networking** → **Generate Domain**.
2. Copy the URL (e.g. `https://job-hunter-production.up.railway.app`).
3. Open your local `config.json` and paste it:
   ```json
   "railway_url": "https://job-hunter-production.up.railway.app"
   ```

---

## Step 6 — Run relay.py on your Mac

`relay.py` watches for local JSON files from your scheduled tasks and forwards them to Railway every 30 seconds.

```bash
cd ~/path/to/job-hunter
python3 relay.py
```

You should see:
```
[relay] Starting Job Hunter relay…
[relay] Railway URL : https://job-hunter-production.up.railway.app
[relay] Sync key    : 6czRcTb…
[relay] Polling every 30 seconds. Press Ctrl+C to stop.
```

**Keep relay.py running whenever your scheduled tasks are running.** You can add it to macOS Login Items or run it in a tmux session if you want it always-on.

---

## Step 7 — Register your admin account

1. Open `https://YOUR-RAILWAY-URL/register` in your browser.
2. Register with `eran.ganot@gmail.com` — the app automatically assigns the `admin` role.
3. Complete the onboarding wizard (upload your CV, set preferences).

---

## How the sync works

```
search_jobs.py  →  writes  pending_jobs.json  →  relay.py  →  POST /api/sync/jobs  →  Railway DB
apply_jobs.py   →  writes  applied_updates.json  →  relay.py  →  POST /api/sync/updates  →  Railway DB
notification    →  writes  notify.json  →  relay.py  →  POST /api/sync/notify  →  Railway sends Telegram/WhatsApp
relay.py        →  GET  /api/sync/approved  →  writes  approved_jobs.json  →  apply_jobs.py reads it
```

All sync API calls use `SYNC_API_KEY` as a shared secret (`?api_key=…` query param).

---

## Troubleshooting

**App crashes on Railway**
→ Check **Deployments → Logs** in Railway dashboard. Most likely cause: missing env var.

**relay.py says "railway_url is not set"**
→ Edit `config.json` and set `"railway_url"` to your Railway domain.

**Jobs aren't appearing on Railway dashboard**
→ Make sure `relay.py` is running on your Mac. Check its output for errors.

**SQLite errors on Railway**
→ Confirm the persistent volume is mounted at `/data` and `DATABASE_PATH=/data/jobs.db` is set.

**Uploaded CVs disappear after deploy**
→ Same fix — make sure `UPLOADS_DIR=/data/uploads` and the volume is at `/data`.
