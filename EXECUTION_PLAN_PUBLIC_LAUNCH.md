# Job Hunter — Execution Plan: Public, Paid, Multi-User

_v3, 2026-09-07. Audited against `origin/main` @ `4e4a04a`. **Status: awaiting approval — no work starts, including the repo relocation, until you say go.**_

Changes in v2: `C:\dev\job-hunter` becomes the canonical clone (was the other way round); staging is an existing Railway environment to be re-aligned, not a new one; **auto-apply stays off and Phase 1 is parked**; payments stays Paddle-vs-Stripe undecided behind an adapter; **UI convergence promoted to Phase 4, entitlements demoted to Phase 5**; new parallel **Workstream A — native Android app**.

Changes in v3, from your four answers: worker runs **in-process** until launch (no second Railway service yet) · **billing gets built but nobody is charged yet** — prices stay hidden until auto-apply works · Android ships as a **sideloaded APK**, no Play Store for now · relocation waits for plan approval rather than running ahead.

---

## Phase map

| # | Phase | State |
|---|---|---|
| 0 | One repo, one baseline, staging aligned | **next** |
| 1 | Make auto-apply real | 🅿️ **parked** (your call, §Appendix A) |
| 2 | Postgres + storage | queued |
| 3 | Multi-user runtime + security | queued |
| **4** | **One UI: the app is the web** ⬆ promoted | queued |
| **5** | **Plans, quotas, entitlements** ⬇ demoted | queued |
| 6 | Payments (provider TBD) | blocked on your Paddle/Stripe call |
| 7 | Public launch readiness | queued |
| A | **Native Android app** (Capacitor, sideloaded APK) | parallel, starts after Phase 4 step 1 |

Locked decisions: platform API keys with hard per-tier quotas · SQLite → Postgres now · tiers split by quotas **and** feature gates · Free has no auto-apply · email verification + password reset + Google sign-in + legal/data controls before public signup · incremental delivery · **CV storage = Postgres `bytea`** · `/dashboard` redirects to `/app` once parity lands · stale clones archived · **worker in-process until launch** · **billing built but not charging** · **Android sideload-only for now**.

---

## 0. Where the code really is

**Three clones existed. Your call: `C:\dev\job-hunter` becomes the only one.** Today it is the *stale* one, so Phase 0 inverts the current state rather than just deleting folders:

| Clone | Today | After Phase 0 |
|---|---|---|
| `C:\dev\job-hunter` | 81 commits behind, no `web/`, no `ingestion/`, no PWA | **canonical** — synced to `origin/main`, carries the uncommitted work |
| `C:\Users\erang\job-hunter` | in sync with `origin/main` (`4e4a04a`) + 3 uncommitted files + `PAID_SOURCES_BENCHMARK.md` | archived (renamed `*_ARCHIVED_2026-09`) |
| OneDrive `…\Eran's dev\Job Hunter` | divergent `master` @ `870c4eb` | archived |

`CLAUDE.md` currently names the `C:\Users\erang` clone as source of truth — it gets rewritten to point at `C:\dev\job-hunter` in the same commit, so no future session repeats this.

**"Make the web look like the app" is a convergence, not a re-skin.** You already built the app UI: a React + Vite + TS + Tailwind PWA in `web/`, shipped as the committed `web_bundle/`, served by `app.py:5509` at `/app`, with swipe review, dark theme, Queue/Applied/Deferred/Passed/Activity/Analytics, push and an install prompt. `/dashboard` is the legacy server-rendered UI: **`app.py` lines 2345–5380, ~3,000 of its 7,554**, are HTML string constants styled by a **frozen gzip+base64 Tailwind blob** at `app.py:33` that no build step regenerates.

So: promote the PWA to be the product on every screen size, close the gap, delete the legacy HTML. That also closes your own BACKLOG #13 instead of doubling down on it — and it's what makes Workstream A (Android) nearly free.

**The gap — legacy-only features to port into `/app`:**

| Missing from `/app` | Endpoint |
|---|---|
| Onboarding flow | `/api/dismiss-onboarding`, `/onboarding` |
| Pipeline stages (Screening / Interviewing / Offer / Rejected) | `/api/set-stage` |
| Bulk select + bulk actions | `/api/jobs/bulk` |
| Change password | `/api/change-password` |
| Manual run-apply batch trigger | `/api/run-apply` |
| Test notification | `/api/test-notification` |
| CV AI analysis (distinct from the optimizer) | `/api/analyze-cv` |
| Admin apply probes | `/api/admin/apply-selftest`, `/api/admin/apply-test`, `/api/admin/inject-jobs` |
| Desktop layout | — (PWA is phone-first) |

Everything else the PWA already calls: jobs + all review actions, `/api/me`, `/api/stats`, `/api/activity`, `/api/run-search`, `/api/upload-cv`, `/api/cv`, `/api/cv-optimizer-analyze`, `/api/save-profile`, `/api/save-schedule`, `/api/save-notifications`, `/api/learned`, `/api/blocklist`, `/api/patterns/forget`, `/api/validate-links`, push, five admin endpoints. Login/register/Google sign-in are server-rendered and shared by both UIs — they get restyled, not rebuilt.

---

## 1. Audit of the real head (2026-09-07)

| Fact | Evidence |
|---|---|
| Stdlib HTTP server, no framework | `app.py` 7,554 lines, `BaseHTTPRequestHandler` + `ThreadingMixIn`, `if path == …` route chains |
| Two UIs | React PWA at `/app` (committed `web_bundle/`, 29 files) + legacy HTML strings |
| Frontend build is manual, undocumented in CI | Railway does **not** build `web/`; STATUS.md records the day a 393-byte CSS shipped because Tailwind never compiled. The ~25KB CSS check and the SW cache bump are load-bearing rituals |
| Storage | SQLite on a Railway volume at `/data` (autocommit + `busy_timeout` 30s after the June lock incident); CVs are files under `UPLOADS_DIR` |
| Migrations | Flat `try/except ALTER TABLE` list in `db.py` — no version table, no ordering, no down-path |
| SQLite-specific SQL | `datetime('now')`, `strftime`, `AUTOINCREMENT`, `INSERT OR IGNORE/REPLACE`, `last_insert_rowid()`, `PRAGMA` — ~35 sites, plus raw `sqlite3` in `apply_engine.py` |
| Background work | Unbounded `threading.Thread` per search/apply **inside the web process** + a 60s scheduler loop in the same process |
| Auth | PBKDF2-SHA256 260k + salt, server-side sessions, **Google Sign-In already shipped** (`/auth/google/start` → `/auth/google/callback`, state + PKCE, links by verified email) |
| Security — BACKLOG P0, re-checked | **Already fixed:** `Secure` cookie (#1, `auth.py:199`), `hmac.compare_digest` (#2, `auth.py:36`), CORS scoped to `MOBILE_URL` (#5), expired-session cleanup (#11, `auth.py:159`). **Still open:** no rate limiting (#3), notification secrets plaintext (#4), and **zero CSRF** (0 matches for `csrf`) — never on the backlog |
| Sourcing | `ingestion/` pipeline with Pydantic normalization, fuzzy dedup, relevance gate — and `ingestion/credits.py` already implements caps + a circuit breaker with role-gated PAID sources. Quotas extend that idea |
| Tests | 15 files, **147 test functions**, CI on GitHub Actions. No route-level tests, no tenant-isolation tests |
| Auto-apply | **Off in production** (`APPLY_ENGINE_ENABLED` unset ⇒ `apply_engine.py:1154` no-ops), `APPLY_MAX_PER_RUN` default 5, root cause open. **Staying off — Phase 1 parked** |
| Scale today | 7 registered users, 3 active (you + 2). Migration risk is low; this is the cheapest moment to move to Postgres |

---

## 2. Payments: still open, and it doesn't block anything until Phase 6

You're undecided, so the plan carries both. All billing code sits behind `billing/provider.py` (create checkout · verify webhook · parse event · fetch subscription · cancel · portal URL) — Phases 0–5 are provider-agnostic, and switching later is a new adapter, not a rewrite.

**What actually separates them for you:**

| | Paddle (merchant of record) | Stripe Billing (you are the merchant) |
|---|---|---|
| Israeli seller | Supported — works with software businesses anywhere outside its sanctions list | Supported |
| VAT / sales tax | Paddle collects and remits worldwide, issues invoices | **Yours.** Stripe Tax calculates, you register and remit per jurisdiction |
| Fees | Higher headline (~5% + fixed, all-in) | Lower (~2.9% + 30¢ + Tax/Billing add-ons) |
| Chargebacks | Paddle's problem | Yours |
| Developer experience | Good | Best in class |
| Note | Stripe's own merchant-of-record product (Managed Payments) **excludes Israel** from its supported business locations, so "Stripe + MoR" isn't on the table | — |

**The deciding question is not technical:** are you willing to own VAT registration and remittance in the countries your customers live in, in exchange for roughly 2–3 points of margin? At your likely first-year volume that difference is small in absolute money and large in evenings spent on tax admin. That's why I recommend Paddle — but it's a business call, and I'll build to whichever you name.

**One new input:** if the Android app ships on the Play Store (Workstream A), Google's June 2026 rules let you link out to your own web checkout for subscriptions in the US/UK/EEA — but a **10% service fee on auto-renewing subscriptions still applies** to purchases attributable to the app, with Play's own billing adding ~5% on top. Sideloading the APK (the AdaptiveFit pattern) avoids that entirely. Decide distribution before you price.

**Deadline:** the provider must be chosen before Phase 6 starts, not before Phase 0.

---

## 3. Target architecture

```
  phone PWA · Android shell · desktop browser ──TLS──► web service (Railway)
                one React app at /app                  app.py: auth, JSON API, bundle
                                                       enqueues work — never runs it
                        ┌──────────────────────────────────┼────────────────────┐
                        ▼                                  ▼                    ▼
                 Postgres (Railway)                 CV bytea in Postgres   billing webhooks
                 data + job queue                   (volume retired)       → subscriptions
                        ▲
                        │ SELECT … FOR UPDATE SKIP LOCKED
                 worker (same image, CMD python worker.py)
                 search · scheduler (advisory-locked) · apply (when un-parked)
                        │
              Gemini · Resend · Telegram/Twilio · Web Push / FCM
```

---

## 4. The phases

### Phase 0 — One repo, one baseline, staging aligned (≈1 session)

1. **Relocate to `C:\dev\job-hunter`:**
   - `git fetch` + discard the CRLF-only working-tree churn + `git pull --ff-only` → 81 commits forward to `4e4a04a`.
   - Carry over the uncommitted work from the old clone (`app.py` apply-test job lister, `STATUS.md`, `.gitignore`, `PAID_SOURCES_BENCHMARK.md`) and verify `git diff` matches byte-for-byte.
   - Rewrite `CLAUDE.md` to name `C:\dev\job-hunter` as the single source of truth; note both archived copies.
   - Rename the other two clones to `*_ARCHIVED_2026-09` (I can't rename a connected folder's own root — that rename is one line for you in Explorer, or I do it via a script you run).
   - Verify: `python -m py_compile app.py`, full suite green (147), `git log -1` = `4e4a04a`.
2. **Align staging with prod** (`railway.com/project/7e3fa534…`, env `6e5667a4…`), which hasn't been touched in a while:
   - Diff staging vs prod: image/Dockerfile, deployed commit, **every env var** (Gemini key, `GOOGLE_CLIENT_ID/SECRET` + redirect URI, `APPLY_ENGINE_ENABLED` — stays unset, `APPLY_MAX_PER_RUN`, `SYNC_API_KEY`, `DATABASE_PATH`/`UPLOADS_DIR`, ingestion flags), volume attached, domain.
   - Redeploy staging from `main`, seed it with a **sanitized copy** of prod data (real shape, scrubbed emails/tokens), smoke-test `/login`, `/app`, `/dashboard`, `/api/health`.
   - Write the differences into `STATUS.md` so staging drift is visible next time.
3. **Back up production**: DB + uploads pulled locally, row counts checked against `/api/health`, and one restore actually rehearsed.
4. **Build the missing safety net** — `tests/test_routes.py`: boot the handler on an ephemeral port; auth redirects on every authed route; the login → `/app` path; **one cross-user isolation case per resource** (user B must 404 on user A's job). This harness verifies every later phase. (BACKLOG #10.)

**Done when:** one clone, clean tree, staging serving the same commit as prod with a sanitized dataset, a restore you've performed, and CI green at 147 + route tests.

### Phase 1 — 🅿️ PARKED: make auto-apply real
Auto-apply stays **off**. Everything about it — the probe sequence, the fix, the honest success metric — is preserved in **Appendix A**, ready to un-park. Downstream consequences are folded into the phases below (tier matrix, pricing, marketing copy).

### Phase 2 — Postgres + storage (≈2–3 sessions)
1. `DATABASE_URL` + `psycopg[binary,pool]`; pool in `db.py`; SQLite retained for local dev behind the same interface until cutover.
2. Compatibility cursor (`?`→`%s`, dict rows) so the ~200 call sites keep working, then a targeted sweep of the SQLite-isms (`datetime('now')`→`now()`, `INSERT OR IGNORE`→`ON CONFLICT DO NOTHING`, `last_insert_rowid()`→`RETURNING id`, `AUTOINCREMENT`→identity, `strftime`→`to_char`), including the raw `sqlite3` in `apply_engine.py`.
3. Numbered migrations (`migrations/0001_init.sql`…) + `schema_migrations` + boot runner, replacing the `try/except ALTER TABLE` list. (BACKLOG #15.)
4. **CV storage → Postgres `bytea`** (your call). CVs move out of the volume; readers materialize a temp file when a form upload needs a path. The volume can then be retired, which also removes the "a volume mounts to only one service" constraint on the worker.
5. `scripts/sqlite_to_pg.py`: table-by-table copy, row-count + checksum verification, idempotent, rehearsed twice on staging against the prod copy.
6. Cutover: maintenance page → final export → import → verify → flip `DATABASE_URL`. SQLite file untouched as rollback. **7 users, 3 active — this is as cheap as this migration will ever be.**

**Done when:** staging runs fully on Postgres with prod data, counts match exactly, suite green, rollback executed once for real.

### Phase 3 — Multi-user runtime + security (≈2 sessions)
With auto-apply parked, Chromium isn't running — but **searches are still minutes-long threads inside the web process**, and the queue is the prerequisite for un-parking apply later.

1. `job_runs` queue table (`user_id, kind, status, attempts, payload, locked_by, locked_at, run_after`); web enqueues, a worker loop claims via `FOR UPDATE SKIP LOCKED`; **max 1 concurrent run per user**; heartbeat + stuck-run requeue (the existing `applying` sweeper moves here with a test).
2. Scheduler moves behind a Postgres advisory lock so two instances can't double-fire a user's daily run.
3. **Worker packaging — in-process until launch** (your call). `worker.py` is a standalone module with its own loop, but it is started as a background process inside the existing service: no second Railway service, no second deploy target to keep in sync. The boundary is written so splitting it out later is a `Procfile`/`CMD` change, not a refactor — which is what un-parking auto-apply will want, since Chromium deserves its own box.
4. Security — what BACKLOG P0 still leaves open, each with a test (#1/#2/#5/#11 are already fixed; don't redo them):
   - Login/register/run-trigger rate limiting (#3).
   - Encrypt `telegram_token` / `twilio_auth_token` / `email_smtp_pass` at rest + one-shot re-encryption migration (#4).
   - **CSRF tokens on every POST** (none today), with the PWA fetch client updated to send them.
   - Session rotation on login; 30-day → 14-day sliding sessions.
   - Tenant-isolation sweep over every SQL site; unscoped `FROM jobs WHERE id IN (…)` / `WHERE status=…` cases resolved; Phase 0 isolation tests extended to cover all of them.
5. Cost guardrails: global daily Gemini spend ceiling, per-user run caps, admin alert on breach. (Gemini 429s already degraded scoring once — STATUS.md, round 8/10.)
6. Observability: `print()` → `logging` (BACKLOG #7, ~166 calls), request logs carrying user id, Sentry, `/api/health` extended with DB check + queue depth + worker heartbeat age.

**Done when:** a long search never blocks a page load, two users' runs don't interfere, CSRF + isolation tests green.

### Phase 4 — One UI: the app *is* the web ⬆ (≈3–4 sessions)
1. **Fix the build story first.** `npm run build:web` that builds, **asserts the CSS is ~25KB not 400 bytes**, bumps the SW cache version, and refreshes `web_bundle/` — with the assertion in CI so a broken bundle can't merge. Everything here rides on it, and it has already bitten you once.
2. **Desktop layouts.** ≥768px and ≥1280px: persistent sidebar instead of bottom nav, multi-column queue, wider job detail — so `/app` is the right answer on a laptop.
3. **Port the legacy-only features** (§0 table): onboarding, pipeline stages, bulk actions, change password, run-apply trigger, test notification, CV analysis, admin probes.
4. **Restyle the shared server-rendered pages** (login, register, Google button, password reset) into the dark app design so the seam disappears.
5. **Flip and delete.** `/dashboard` → 302 to `/app` behind a `LEGACY_UI=1` escape hatch for one release; then remove `DASHBOARD_HTML`, `SETTINGS_HTML`, `ONBOARDING_HTML`, `ADMIN_HTML` and the base64 Tailwind blob — ~3,000 lines out of `app.py` (2345–5380), closing BACKLOG #13.
6. **Verify:** Playwright screenshots at 390 / 768 / 1280 px; contrast check on the dark palette; a `mobile-qa` pass on your Android device (swipe, install, push deep-links, session continuity phone↔desktop).

Plan badges, usage meters and locked states are **not** built here — they land in Phase 5 as a deliberate second pass over the same components. That's the cost of promoting this phase, and it's a few hours, not a rebuild.

**Done when:** one UI on every device, nothing reachable only from the old pages, `app.py` a third smaller, suite green.

### Phase 5 — Plans, quotas, entitlements ⬇ (≈2 sessions)
1. `plans.py` — catalogue in code, one dict per plan, editable without a migration.
2. Tables: `subscriptions`, `usage_counters`, `entitlement_overrides`, `billing_events` (webhook idempotency).
3. `entitlements.py`: `check(user, feature) → (bool, reason)`, `consume(user, meter, n) → (bool, remaining)` — modelled on `ingestion/credits.py`. Enforced **server-side** at every metered entry point: run-search, cover letter, CV optimizer, schedule frequency, notification channels, paid ingestion sources (and apply, when un-parked). The UI renders what the API reports; it never decides access.
4. `/api/me` returns plan + features + live usage; the PWA gets meters, locked states, upgrade CTAs.
5. Admin console: plan per user, grant credits, per-user usage + estimated AI cost, impersonate for support.
6. Grandfather all 7 registered users onto the `beta` plan; your account → `admin`. When pricing goes live, the 2 active users become design partners on a free Expert year.

**Starting matrix — note what auto-apply being parked does to it:**

| | Free | Premium | Expert | Admin |
|---|---|---|---|---|
| Search cadence | weekly | daily | daily + on-demand | unmetered |
| Scored matches | 25/wk | 150/wk | 500/wk | unmetered |
| **Auto-apply** | ✗ | 🅿️ *not sold while parked* | 🅿️ *not sold while parked* | unmetered when on |
| Cover letters | 1/mo | 20/mo | 200/mo | unmetered |
| CV optimizer | ✗ | 2/mo | 10/mo | unmetered |
| Notifications | email | + push, Telegram, WhatsApp | all, priority | all |
| Pipeline stages + analytics | basic counts | full | full + export | full |
| Paid ingestion sources | ✗ | ✗ | ✓ (credit-capped) | ✓ |
| History retention | 30 days | 12 months | unlimited | unlimited |

**Your call on monetisation: build the machinery, charge nobody yet.** Every user sits on a plan and every quota is enforced from this phase on — but prices stay hidden, no checkout is exposed publicly, and the paid tiers are dark until auto-apply works (Appendix A). Concretely:

- Phase 5 ships with all four plans live and everyone assigned to a **`beta`** plan that carries Premium-level quotas at no cost. Nothing to refund, no promise made that the product can't keep.
- The quota counters start recording immediately, so by the time you do price, you'll know what a real user actually consumes per month — which is the number that makes pricing a calculation instead of a guess.
- Phase 6 builds and tests checkout end-to-end in **sandbox only**; going live is a config flip plus publishing `/pricing`, not new code.
- Flip to charging when auto-apply has a measured success rate you're willing to put in writing.

**Done when:** flipping a plan in admin changes what the API allows on the next request, and an exhausted quota returns a clean paywall response — never a 500 — on every gated path.

### Phase 6 — Payments, sandbox-only (≈2 sessions) — *blocked on your provider call*
Built and proven, but not switched on: no public `/pricing`, no live keys, `BILLING_LIVE=0`.

1. Account + sandbox; Premium/Expert products, monthly + annual, USD, SaaS tax category.
2. `/pricing` behind the admin flag; hosted checkout with `user_id` in metadata.
3. `POST /webhooks/<provider>`: signature verification, idempotency via `billing_events`, handlers for created/updated/canceled/paused, payment succeeded, payment failed.
4. Subscription state → entitlements: `active`, `past_due` (7-day grace + banner), `canceled` (access to period end, then downgrade job), `paused`.
5. Billing settings in-app: plan, renewal date, invoices, change plan, cancel — via the provider's customer portal.
6. Tests: bad signature rejected, duplicate delivery, out-of-order events, downgrade at period end, mid-cycle upgrade proration, webhook arriving before checkout returns.

**Done when:** a sandbox purchase upgrades a real staging account end-to-end, cancellation downgrades at period end, replaying every webhook fixture twice changes nothing — and going live is a flag plus live keys, with no code left to write.

### Phase 7 — Public launch readiness (≈2 sessions)
1. Email verification on signup + password reset (Resend already wired). Google sign-in exists — confirm `GOOGLE_CLIENT_ID`/`SECRET` on Railway and register the redirect URI for the launch domain.
2. Legal: Terms (including "you authorise Job Hunter to submit applications on your behalf" — worded for the parked state and ready for un-parking), Privacy Policy naming sub-processors (Google Gemini, Resend, Twilio, Railway, the payment provider), cookie notice. Drafts for your review — not legal advice.
3. Data controls: export my data; delete my account (cascade CV, jobs, sessions, push subscriptions, subscription cancellation); retention policy. **Play Store listing requires a public privacy policy URL** — Workstream A depends on this item.
4. Public landing + pricing, real domain, `robots.txt`, OG tags.
5. Funnel analytics: signup → CV uploaded → first search → first approve → paywall hit → upgrade.
6. Runbook in `STATUS.md`: restore a backup, drain the worker, roll back a deploy, rotate keys, flip the apply kill-switch.

---

## Workstream A — Native Android app (parallel, ≈1–2 sessions)

Cheap, because you've done it before and because Phase 4 makes `/app` a proper app on every size. **Reuse the AdaptiveFit pattern verbatim:** Capacitor 6, Android only, WebView pointed at a live URL rather than bundling the web code (`adaptivefit-mobile/capacitor.config.ts` is the template, including the `CAPACITOR_DEV` LAN-dev switch).

1. `npx cap init` in the repo (or a sibling `android/` project), `server.url` → `https://<domain>/app`, production HTTPS only, cleartext limited to LAN in dev.
2. Icons + splash via `@capacitor/assets`; status-bar and safe-area handling to match the dark theme.
3. **Push:** the PWA's Web Push doesn't carry over to a Capacitor WebView — swap to `@capacitor/push-notifications` + FCM for the native shell, keeping Web Push for browser/PWA users. One `deliver_notification()` branch on subscription type.
4. External job links open in the system browser, not the WebView.
5. Deep links: `/app#applied` etc. from a push tap; Android App Links if a custom domain lands.
6. Signing keystore + release build; store it somewhere you won't lose it.
7. **Distribution: sideloaded APK** (your call — the AdaptiveFit pattern). Zero store fees, no review, no privacy-policy gate, ships in a session. You and the 2 active users install directly; updates to the web UI land instantly since the shell only points at a URL, and only shell changes need a new APK.

   Play Store stays a post-launch option, and its cost is worth knowing before you price: from June 30 2026 Google's expanded billing choice lets you link out to your own checkout in the US/UK/EEA, but a **~10% service fee on auto-renewing subscriptions attributable to the app still applies** (Play's own billing adds ~5% on top), plus a Play Console account, a public privacy policy, data-safety disclosures and review. Since billing isn't going live until auto-apply works, this decision can wait — revisit it after Phase 7, when the privacy policy exists anyway.

**Dependency:** starts after Phase 4 step 1 (a reliable bundle build). Everything else is independent of Phases 5–7, so it can run alongside them.

---

## 5. Sequencing

**0 → 2 → 3 → 4 → 5 → 6 → 7**, with A branching off after 4.1, and 1 parked.

The one wrinkle in promoting Phase 4: plan badges, meters and locked states are a second pass over components you'll have just built. It's a few hours, not a rebuild — and in exchange you get the thing you actually want to look at, several sessions earlier.

---

## 6. Answers to your open questions

**Q5 — Does `relay.py` survive?**
`relay.py` is a Mac-side loop that every 30s pushes `pending_jobs.json` / `applied_updates.json` / `notify.json` to `/api/sync/*` and pulls `/api/sync/approved` back — from the era when the scheduler ran on your Mac. The server has had its own scheduler for a long time, so as a *scheduler bridge* it is dead weight.

- **Keep it** if you want a way to feed jobs into the app from a machine that has something Railway doesn't — most concretely the **SecretJobs logged-in session** (parked in BACKLOG for exactly this reason: no public feed). Then `/api/sync/jobs` becomes a deliberate authenticated ingest API, not legacy.
- **Retire it** and you delete `relay.py` plus four `/api/sync/*` endpoints that no test covers and that authenticate only on a shared `SYNC_API_KEY` — a smaller public surface before you invite strangers in.
- **Recommendation:** retire `relay.py` (the file and the Mac workflow) in Phase 3, but **keep `/api/sync/jobs` behind the API key**, documented and tested, as the local-ingest hook. You lose the cron-bridge cruft, keep the one capability that only a logged-in machine can provide.

**Q6 — "Even with the current amount of users?"**
My "triples the footprint" line was wrong, and you were right to push. Railway bills **consumed resources, not service count**: $10/GB RAM/month, $20/vCPU/month, $0.05/GB egress, $0.15/GB volume — Hobby is $5/mo including $5 of usage, Pro $20 including $20. At 7 registered / 3 active users:

- Postgres idles at a few hundred MB → roughly **$2–5/month**, and it lets you drop the volume.
- A worker service that mostly sleeps (auto-apply parked, so no Chromium) costs **cents when idle** — you pay for what it burns, not for existing.
- Staging costs only what it consumes; sleeping between tests, that's small.

So the realistic delta is **single-digit dollars a month**, likely absorbed by the included credit on your current plan. Which is also why question 2 (separate worker service vs in-process) is a *simplicity* call, not a cost one.

**Answered by you:** custom domain — yes (name TBD) · CV storage — Postgres `bytea` · archive the stale clones — yes · redirect `/dashboard` → `/app` after parity — yes · users: 7 registered, 3 active · worker in-process until launch · billing built but dark · Android sideload-only · relocation waits for approval.

**Still TBD, and now safely deferrable:** price points and trial. Because billing ships dark, both can be decided later with real usage data from the quota counters instead of guessed now. The one date that matters: pick the payment provider before Phase 6 starts.

---

## 7. Risk register

| Risk | Why it matters here | Mitigation |
|---|---|---|
| **Selling a product whose flagship feature is parked** | Premium's value would rest on discovery + triage alone | Resolved: billing is built but dark — everyone on `beta`, prices hidden until auto-apply has a measured rate worth writing down |
| Building billing that then sits unused for months | Provider APIs drift; untested webhook code rots | Keep the sandbox integration covered by the webhook fixture tests in CI, so the day you flip it live the suite still proves it |
| Broken frontend bundle ships silently | Already happened (393-byte CSS) | Build assertion + SW cache bump in CI (Phase 4.1) |
| Staging has drifted from prod | Untouched for months; a stale staging is worse than none — it green-lights broken changes | Phase 0.2 diffs image, commit and every env var, then redeploys from `main` |
| Postgres migration data loss | Only copy of every user's history | Two staging rehearsals with prod data, verified counts, SQLite retained, rollback executed once |
| Play Store fee/policy surprise | ~10% on subscriptions attributable to the app even with external checkout | Decide distribution (question 4) before pricing; sideload avoids it |
| `app.py` truncation on the Windows mount | Documented in your `safe-windows-edits` skill | Idempotent patch scripts + `py_compile` + `git diff --stat` size check |
| AI spend scales with signups, not revenue | Platform keys, your card | Server-side quotas, daily ceiling + alert, per-user cost in admin |
| CVs are personal data | GDPR applies the moment one EU user signs up | Encryption at rest, export/delete, retention limits, sub-processors disclosed |
| Single region, single instance | Railway box down = everyone down | Health checks + alerting, documented restore |

---

## 8. Non-goals

No framework migration (Flask/FastAPI) — revisit after launch. No iOS. No new job sources or scoring changes (SecretJobs stays parked). No i18n / Hebrew UI. No team or multi-seat accounts.

---

## 9. Working discipline

Bugs found during this work follow your rule: root cause **proven by observation** — reproduction or live logs, candidate causes ruled out with the observation that killed each, exact code/state, blast radius, why it was silent — before any fix is written. Proven → fix in the same turn; only inferred → I stop and check with you. Root causes and what was ruled out go into `STATUS.md` when the fix ships.

Skills in play: `safe-windows-edits` (every large edit), `ship-it` (every deploy, incl. the `web_bundle` rebuild + SW cache-bump rules), `investigate-issue` + `app-bug-triage` (bugs), `engineering:testing-strategy` (Phase 0), `engineering:system-design` (this doc), `design:design-system` / `design:ux-copy` / `design:accessibility-review` / `mobile-qa` (Phase 4 + Workstream A), `product-tracking-skills` (Phase 7), `project-status-log` (STATUS.md upkeep).

---

## Appendix A — 🅿️ Parked: make auto-apply real

Preserved so un-parking is a decision, not a re-investigation. When you want it:

1. `GET /api/admin/apply-selftest` on production as admin — Playwright import, live Chromium launch, Gemini key, DB/CV presence, kill-switch state, `queue_audit`. Read-only, submits nothing.
2. `GET /api/admin/apply-test?job_id=<id>` — dry run; `&mode=live` submits exactly one job.
3. Write the finding your way: reproduction, each candidate cause ruled out with the observation that killed it, exact code/state, blast radius, why it was silent → `STATUS.md`.
4. Fix only what the evidence names. Re-enable with `APPLY_ENGINE_ENABLED=1` and a low `APPLY_MAX_PER_RUN`.
5. Measure before selling: of approved jobs with a resolvable direct-ATS URL, what fraction submit successfully? That number sets what Premium may promise. Job-board listings correctly return `manual_required` — the copy must say "auto-apply where the company's ATS allows it, manual link otherwise."

Standing context: `APPLY_ENGINE_INVESTIGATION.md` says infra was proven healthy (Playwright, Chromium, key, volume) and pointed at queue composition; STATUS.md rounds 4–11 then rebuilt the ATS resolver, added Gemini 429 backoff, and shipped the guarded dry-run/live endpoint. The next observation, not the next code change, is what un-parks this.

---

## 10. Approval

Say go and I start with **Phase 0**, one sub-phase per session, each landing on staging with its verification evidence before it goes near production.
