# 🎯 Job Hunter

An AI-powered personal job search assistant that runs entirely on **Railway** (cloud). It automatically finds relevant positions, lets you review and approve them, then applies on your behalf — and notifies you via WhatsApp every step of the way.

---

## Features

- **Multi-round AI job search** — Claude runs a separate web search per job title, covering 9 sources per round, and deduplicates results against your full job history
- **Smart scoring** — every job card shows a **match %** (how well the role fits your skills) and a **candidate score** (how strong a candidate you are)
- **🔗 Automatic URL verification** — every job URL is checked on import; dead links are flagged with ⚠️ before you review them. Historical jobs with unverified URLs are also re-checked on every search run
- **Job status check** — one-click 🔍 button fetches the original posting and asks Claude if the role is still open
- **Review queue** — approve, pass, or defer jobs from a clean mobile-friendly dashboard
- **🤖 Auto-apply** — approved jobs are submitted directly by Railway using Playwright + Claude AI: fills forms, uploads your CV, and verifies the result page confirms submission
- **Apply status badges** — each Applied card shows ✅ Confirmed / 📤 Submitted / 👤 Manual needed / ❌ Failed
- **Interview pipeline** — track applied jobs through Screening, Interviewing, Offer, and Rejected stages
- **Two consolidated notifications** — one WhatsApp at the end of each search run (digest of new jobs + URL status), one at the end of each apply run (full outcome breakdown)
- **Tab deep-links** — notification links open the exact dashboard tab (Review or Applied) automatically
- **Scheduled runs** — configure daily search and apply windows; view the next run time from the dashboard

---

## How It Works

### 1. Multi-Round Job Search
For each job title in your profile, Claude runs a separate web search using Anthropic’s `web_search` tool. Results are deduplicated against your full job history (not just the current run) before being inserted. Each round targets 5–8 unique jobs; a typical 4-title profile yields 15–25 fresh results per day.

**Sources searched per round:**
- LinkedIn Jobs (linkedin.com/jobs)
- Glassdoor (glassdoor.com/job-listings)
- AllJobs.co.il
- Drushim.co.il
- Wellfound / AngelList (wellfound.com/jobs)
- Comeet (comeet.co)
- Greenhouse job boards (boards.greenhouse.io)
- Lever job boards (jobs.lever.co)
- Company career pages directly

### 2. URL Verification (Automatic)
After each search run, URLs are checked in parallel (8 concurrent workers, 8 s timeout) for:
- **All new jobs** just imported this run
- **All existing jobs** in your history that haven’t been verified yet (`url_verified IS NULL`)

Each job is marked **🔗 URL OK** or **⚠️ Dead link** — visible immediately on every card.

### 3. Review & Approve
Open the dashboard → **Review** tab. Each card shows title, company, location, match %, candidate score, source, and URL status. Approve, pass, or defer each job.

### 4. Application Submission (Playwright + Claude)
When the scheduled apply window runs, approved jobs are submitted directly from Railway:

1. **Extract applicant data** — Claude reads your CV + profile to pull name, email, phone, LinkedIn, GitHub, and a short bio
2. **Navigate to the job URL** — Playwright opens a headless Chromium browser on Railway
3. **Blocker detection** — login walls and CAPTCHAs are detected and flagged as `manual_required`
4. **Find Apply button** — clicks common apply-button patterns if the form isn’t already visible
5. **Claude-powered form fill** — the full page HTML is sent to Claude, which returns a JSON plan of `fill`, `select`, `upload`, and `submit` instructions
6. **Execute + verify** — Playwright follows the plan and checks the result page for confirmation phrases

Apply outcomes:

| Status | Meaning |
|--------|---------|
| `confirmed` ✅ | Result page confirmed the application was received |
| `submitted` 📤 | Form was submitted but no clear confirmation phrase detected |
| `manual_required` 👤 | Login wall, CAPTCHA, or unrecoverable blocker found |
| `failed` ❌ | Playwright error or unexpected exception |

### 5. Notifications (2 total, no duplicates)

**After each search run — 1 message:**
```
🔍 Search Complete — 2026-03-22

📋 4 new job(s) added for review:
  • VP Product @ TechCorp 🔗
  • Head of Product @ StartupX ⚠️
  • CPO @ ScaleUp 🔗
  ...

🔄 Re-checked 12 existing job URL(s):
  ✅ 9 alive  ❌ 3 dead

📱 Dashboard: https://your-app.railway.app/dashboard#new
```

**After each apply run — 1 message:**
```
🚀 Apply Run Complete — 2026-03-22
📊 5 application(s) submitted

✅ 2 Confirmed:
  • VP Product @ Company A
  • Head of Product @ Company B

📤 1 Submitted (awaiting confirmation):
  • Director of Product @ Company C

👤 1 Need Manual Apply:
  • CPO @ Company D

❌ 1 Failed:
  • Head of Product @ Company E

📱 Dashboard: https://your-app.railway.app/dashboard#applied
```

---

## Setup

### Environment Variables (Railway)

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Claude API key (required) |
| `TWILIO_SID` | Twilio account SID for WhatsApp |
| `TWILIO_TOKEN` | Twilio auth token |
| `TWILIO_FROM` | Twilio WhatsApp sender (`whatsapp:+14155238886`) |
| `TWILIO_TO` | Your WhatsApp number (`whatsapp:+972...`) |
| `APP_PASSWORD` | Dashboard login password |
| `SECRET_KEY` | Flask session secret key |
| `RAILWAY_PUBLIC_DOMAIN` | Set automatically by Railway — used for notification deep-links |

### Profile Setup (Dashboard → Profile)
- **CV / Resume** — full text; used for job matching and form-filling
- **Job Titles** — comma-separated (e.g. `VP Product, Head of Product, CPO`); each title gets its own search round
- **Keywords** — extra search terms
- **Email** — used when filling application forms
- **CV File** — PDF/DOCX uploaded to Railway; attached to file-upload fields

### Schedule (Dashboard → Schedule)
Set your preferred search time and apply window. Railway keeps the process running 24/7.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python / Flask |
| AI | Anthropic Claude (claude-opus-4-5) + web_search tool |
| Browser automation | Playwright (Chromium, headless) |
| Database | SQLite (persistent Railway volume) |
| Notifications | Twilio WhatsApp API |
| Hosting | Railway (always-on) |

---

## Database Schema — Key Columns

| Column | Description |
|--------|-------------|
| `url_verified` | 1 = URL alive, 0 = dead (checked on every search run) |
| `url_check_date` | ISO timestamp of last URL check |
| `apply_status` | `confirmed` / `submitted` / `manual_required` / `failed` |
| `apply_confirmation` | Confirmation text snippet from result page |
| `apply_attempts` | Number of submission attempts |
| `apply_error` | Error or blocker description if not confirmed |

---

## Local Development

```bash
git clone https://github.com/eranganot/job-hunter
cd job-hunter
pip install -r requirements.txt
playwright install chromium
python app.py
```

Set environment variables in a `.env` file or export them in your shell before running.
