# Job Hunter — Mobile App Plan (PWA)

**Prepared for:** Eran
**Date:** June 15, 2026
**Status:** Final draft for approval — awaiting go on Phase 1

---

## 1. Goal

Turn Job Hunter into an **installable Android PWA** (the AdaptiveFit pattern), featuring the new Tinder-style **swipe** design you attached. v1 native capabilities: **push notifications**, **swipe gestures**, **resume/file upload**. For now it's for **your phone only — no app store**.

### Two non-negotiables (will be treated as hard requirements throughout)
1. **The existing web app keeps working for all users, unchanged.** The new UI is additive (a new `/app` route + a service worker scoped *only* to `/app`). The legacy `/dashboard` and every existing flow stay exactly as they are.
2. **One identity across phone and web.** The PWA runs on your real Railway domain and uses the **same cookie session** as the browser, hitting the same backend and SQLite. Logging in on the phone or on the web is the same account — no second user system.

---

## 2. What we're starting from (current architecture)

| Layer | Reality today |
|---|---|
| Backend | Pure-Python `http.server` (stdlib, no Flask), one ~339KB `app.py`, SQLite, Playwright for auto-apply, Resend for email |
| Hosting | Railway, via Docker |
| Frontend | Server-rendered HTML strings embedded in `app.py`, Tailwind (CDN), button-based dashboard at `/dashboard` |
| **API** | **Already clean** — `/api/jobs`, `/api/jobs/{id}/{approve\|reject\|later\|applied\|retry\|restore}`, `/api/jobs/bulk`, `/api/me`, `/api/upload-cv`, `/api/analyze-cv`, `/api/save-profile`, `/api/save-schedule`, `/api/save-notifications`, `/api/run-apply` |
| Auth | Cookie-based sessions (`auth.py`, PBKDF2) — reused as-is by the PWA |
| Notifications | `deliver_notification()` → email (Resend) + dashboard link. **No web push / service worker / manifest yet** |
| New design | React/TSX SPA (`Job_Hunter_New_Look.tsx`), `motion` + `lucide-react`, dark theme, swipe view + dashboard (Queue/Applied/Deferred/Passed/Activity/Analytics) — currently on mock data |

**Key fact:** the backend already speaks JSON for every action the new design needs. This is mostly a frontend project plus a thin push layer.

---

## 3. Why PWA (not a native wrapper)

You're **Android-only** and testing **on your own phone**. On Android, Chrome PWAs support everything in v1 — Web Push, file pickers, touch/swipe, install-to-home-screen. The usual reason to avoid PWAs (weak iOS web push) doesn't apply to you. PWA is therefore the lighter path:

- No APK, keystore, signing, or Android Studio.
- Instant updates on deploy — no reinstall.
- One codebase, no native project to maintain.
- Web Push needs only **VAPID keys** (I generate them) — no separate Firebase app required.

---

## 4. Target architecture

```
 Your Android phone
 ┌────────────────────────────┐
 │ Installed PWA (home screen)│
 │  • React swipe SPA at /app │
 │  • manifest.json (icon,    │
 │    standalone, dark theme) │
 │  • service worker (scope:  │
 │    /app ONLY) → push +     │
 │    offline shell           │
 └──────────────┬─────────────┘
                │ HTTPS, same cookies as browser
                ▼
 Railway: Python app.py
   • /app        → serves React SPA            (NEW)
   • /app/sw.js  → service worker (scoped)     (NEW)
   • /api/*      → JSON                         (exists)
   • /dashboard  → legacy UI                    (UNTOUCHED)
   • /api/push/register + send on notify        (NEW)
   SQLite + Playwright (unchanged)
```

---

## 5. Workstreams & detailed tasks

### A — Build the React SPA (frontend)
1. Scaffold **Vite + React + TS + Tailwind** in a new `/web` folder in the repo.
2. Port `Job_Hunter_New_Look.tsx`; add `motion`, `lucide-react`.
3. Replace `mockData` with an API client on the existing endpoints:
   - Approve → `POST /api/jobs/{id}/approve`
   - Pass + reason → `POST /api/jobs/{id}/reject {reason}`
   - Defer → `POST /api/jobs/{id}/later`
   - Lists → `GET /api/jobs?status=...`
   - Settings → `/api/save-profile`, `/api/save-schedule`, `/api/save-notifications`, `/api/upload-cv`
   - Analytics → computed from `/api/jobs`, plus one small summary endpoint if cleaner.
4. **Swipe gestures** via `motion` drag (right = approve, left = pass, down = defer); buttons kept as fallback.
5. Auth: call `/api/me`; if 401, redirect to existing `/login`. Same cookie = same user on phone and web.

### B — Serve the SPA from the backend (without touching legacy)
1. Add `/app` + asset routes to `app.py` serving the built bundle.
2. Update Dockerfile to build `/web` and copy assets in.
3. **Regression gate:** verify `/dashboard`, login, onboarding, auto-apply all behave identically. This is a release blocker.

### C — Make it an installable PWA
1. `manifest.json` (name, icons, `display: standalone`, dark theme color, start_url `/app`).
2. **Service worker scoped to `/app` only** — caches the app shell for offline, handles push. Scope discipline so it can never intercept `/dashboard` or other users' requests.
3. Install prompt / "Add to Home Screen" guidance for Android Chrome.

### D — Push notifications (Web Push, net-new)
1. Generate **VAPID** key pair; store server key in env, ship public key to the SPA.
2. SPA: request notification permission, subscribe via the service worker, send subscription to backend.
3. **Backend:** `POST /api/push/register` stores `{user_id, subscription}` in SQLite.
4. **Backend:** extend `deliver_notification()` so it *also* sends a Web Push (alongside the existing email) on new-job-found and apply-status events.
5. Tapping a push deep-links into the right tab (`/app#new`, `/app#applied`).

### E — Native-feel features
- **File upload:** the CV `<input type=file>` triggers Android's file/Photos picker natively in the PWA; posts to existing `/api/upload-cv`. No extra plumbing.
- **External job links:** open in the system browser, not inside the installed app.

### F — QA & verification (on your real Android device)
1. Full swipe loop, settings save, CV upload, push receipt + deep-link, login/session continuity phone↔web, offline shell.
2. **Legacy regression:** confirm other users' `/dashboard` experience is byte-for-byte unaffected.
3. Sanity-check the auto-apply pipeline still runs.

---

## 6. Prerequisites — resolved from your answers

| Item | Status |
|---|---|
| App store release | **Not needed** — install via Add to Home Screen on your phone |
| Push infrastructure | I'll **scaffold it** — VAPID keys, no separate Firebase app required |
| Repo / route / Dockerfile changes | **Approved** — strictly additive, legacy must keep working |
| Privacy policy | **N/A** without a store (I can still generate a short one if you ever want it) |
| App icon / branding | **I'll generate one** from the design's heart + purple/pink gradient motif |

---

## 7. Sequencing

| Phase | Scope | Outcome |
|---|---|---|
| 1 | React SPA wired to the real API, served at `/app` | Open the new swipe UI in your phone browser, logged in as you |
| 2 | PWA manifest + scoped service worker + install | "Add to Home Screen" → app icon, standalone, offline shell |
| 3 | Web Push (VAPID + backend send) | Real push for new matches / apply status |
| 4 | Polish (icon, splash color, deep-links, QA) | Ship-ready for personal use |

Phase 1 alone gives you something to look at and approve before we layer on the PWA/push pieces.

---

## 8. Open items to confirm (small)

1. **Analytics summary endpoint:** OK to add one small read-only `/api/...` for the Analytics tab? (Everything else reuses existing endpoints.)
2. **Service worker scope:** I'll scope strictly to `/app`. Confirm you're fine with the new routes living under `/app` (start_url `/app`).

Neither blocks Phase 1.

---

## 9. After approval

On your go, I start **Phase 1**: scaffold the SPA, port the design, replace mock data with your live API, and serve it at `/app` so you can open it on your phone (logged in as your real account) before we add the PWA shell and push. We review, then continue to Phases 2–4.
