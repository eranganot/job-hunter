# Job Hunter — Execution Plan: next wave

**Status:** approved 2026-10-25 for Steps 0–3. Step 4 waits for Eran's approval of the tier matrix (section 7).
**Date:** 2026-10-25 · **Repo state:** `main` @ `d652482` (2026-10-12)
**Supersedes:** `BACKLOG.md` (June audit, 13 of 20 items since shipped). `EXECUTION_PLAN_PUBLIC_LAUNCH.md` stays the reference for Phases 5–7; this plan sequences what is left of it plus the new work.

---

## 1. Decisions locked on 2026-10-25

| Decision | Detail |
|---|---|
| Payments provider = **Paddle** | Stripe does not onboard Israeli entities without a US company. Paddle is merchant of record and files VAT worldwide. Sits behind `billing/provider.py`, so it is swappable later. |
| **Android APK parked** | `/app` is already an installable PWA (manifest, standalone, icons, service worker with web push). A Capacitor wrapper adds little until the Play Store is in play. |
| **Direct company links** (new P1) | Every relevant job should link to the posting on the company's own site or ATS, not LinkedIn/Indeed. |
| Unresolvable link | **Keep the job, flag it ("No direct link"), rank it a bit lower.** |
| Scope of the link fix | **New jobs + jobs still New/Approved.** Applied and passed history is not touched. |
| **Auto-apply** | Un-parked as **P2, after Phase 5**. The link fix is its first prerequisite. |
| **Shipping** | Claude commits and pushes to `main` directly; Railway deploys on push. Eran no longer runs PowerShell ship blocks. |
| Phase 5 | Marked **super important** by Eran. |
| Big-Tech sources | Promoted to **P1**. |
| Order | **Links first** (Steps 1–3), then Phase 5. |
| "No direct link" penalty | **5 points**, approved. |
| Tier matrix | **Needs Eran's explicit approval before Step 4 starts.** |
| Domain | Not chosen yet — Step 5 waits for it; Steps 4 and 6 move ahead of it if needed. |
| Every change | Ships as a **pull request with a smoke test**, before and after merge. |
| Feedback-loop metrics | Promoted to **P2**. |
| `http.server` replacement | **Parked.** |

---

## 2. Order of work

Priority first, then value, then effort. "Session" ≈ one focused working block.

| Step | Item | Pri | Effort | Depends on |
|---|---|---|---|---|
| 0 | Housekeeping: push access, Mac-era CLAUDE.md, new BACKLOG.md, smoke runnable from here, Sentry on | P1 | S (½ session) | Eran: Sentry DSN, approve repo push |
| 1 | **Direct company links** | P1 | L (3 sessions) | 0 |
| 2 | Link checker: dead postings read as alive | P1 | M (1 session) | 1 (same module, same data) |
| 3 | Big-Tech sources (Google, Apple, Microsoft) | P1 | S–M (1 session) | 1 (shares the resolver) |
| 4 | **Phase 5 — plans, quotas, entitlements** | P1 | L (2 sessions) | 0 |
| 5 | Email verification + password reset | P1 | M (1 session) | **custom domain** (see 4.5) |
| 6 | Legal pages + data export / account deletion | P1 | M (1 session) | 4 (deletion must cancel a subscription) |
| 7 | Broad-exception audit | P1 | M (spread out) | runs inside every step, finished as its own pass at the end |
| 8 | **Un-park auto-apply** (incl. apply-flow UX) | P2 | L (2–3 sessions) | 1, 4 |
| 9 | Delete legacy HTML from `app.py` | P2 | M (1 session) | — |
| 10 | Domain + landing page + funnel analytics | P2 | M (1 session) | domain bought (needed by 5 anyway) |
| 11 | Payments, Paddle sandbox (Phase 6) | P2 | L (2 sessions) | 4; **Paddle account approved** |
| 12 | Feedback-loop effectiveness metrics | P2 | M (1 session) | — |
| 13 | Capture salary at pass time | P3 | M | — |
| 14 | Apply self-test reports stale SQLite fields | P3 | S | do inside 8 |

**Parked:** `http.server` replacement · Android APK · SecretJobs source (waiting on Eran's feed capture).

**Total to the end of P1 (steps 0–7): about 10–11 sessions.**

### Why links come before Phase 5 (confirmed by Eran)
Links change Eran's queue every day and are the prerequisite for auto-apply; Phase 5 only matters once strangers sign up, and signups are also gated on steps 5–6.

**Domain not chosen yet:** if it is still open when Step 5 comes up, Step 6 runs first and Step 5 waits.

---

## 3. How each step ships — one pull request per sub-step, smoke-tested twice

1. Read `STATUS.md`; branch from `main` (`step-<n>-<slug>`); write the change with tests.
2. **Before merge**
   - Full suite passes (`pytest`; currently 728 passed / 16 skipped). Never open a PR red.
   - **Local smoke:** boot the app in the workspace against a throwaway database and run `scripts/smoke.py` against it (health, auth gates, service-worker version, plus the step's own checks).
   - Open the PR with: what changed, test count, local smoke output (last ~20 lines), and the step's "done when" check.
3. **Merge** to `main` → Railway deploys.
4. **After merge — production smoke:** `scripts/smoke.py` against production; it must show `/api/health` `commit` equal to the merge SHA, then the step's own check. The result is posted as a comment on the PR.
5. A failed production smoke = revert PR opened immediately, root cause investigated per the investigate-issue rule before a second attempt.
6. Update `STATUS.md` in the same PR (what ships, root causes and what was ruled out).

Any `web/src` change also rebuilds `web_bundle/`, checks CSS ~25 KB, bumps the service-worker cache and runs `verify_web_bundle.py` — Railway does not build the frontend.

Data changes that touch Eran's rows are done as migrations (keyed, idempotent, tested), as `m0012` was — never through the API.

---

## 4. Steps in detail

### Step 0 — Housekeeping (½ session)

1. **Push access.** Attach `eranganot/job-hunter` with push rights to this workspace. Eran approves the prompt once.
2. **CLAUDE.md for the Mac era.** Drop the `C:\dev` single-clone rules and the "sandbox can't push, hand Eran PowerShell" rule; replace with the push loop in section 3. Keep the two-UI and domain-fact sections.
3. **Smoke from here.** The checks live in `scripts/smoke*.ps1`. Port the read-only parts (health, commit match, anonymous auth gates, service-worker version) to `scripts/smoke.py` so every deploy can be verified from the cloud workspace. The `.ps1` files stay for now.
4. **BACKLOG.md** replaced by the list in section 2, with a pointer to this plan.
5. **Sentry on.** Eran creates a free Sentry project and adds `JH_SENTRY_DSN` to Railway (production and staging). Claude then triggers a test error via an admin-only route and confirms it lands in Sentry.

**Done when:** Step 0's own PR goes through the full section-3 loop — local smoke, merge, production smoke posted on the PR — and a test error appears in Sentry.

### Step 1 — Direct company links (3 sessions)

**What exists today (from code; production numbers not yet measured):**
- The search loop pulls LinkedIn's public guest search (`app.py` ~1312) and Indeed, and stores the `linkedin.com/jobs/view/…` URL as `jobs.url`.
- `apply_engine.resolve_ats_application()` already maps company + title to the exact posting on Greenhouse, Lever, Ashby, SmartRecruiters and Comeet, using a curated slug map, name-derived slugs and Gemini-suggested slugs (each verified against the live ATS API).
- **It only runs inside the apply engine, which is switched off**, so the stored link is never replaced.
- It cannot see Workday tenants or companies running their own careers site.

**1a. Measure first (part of session 1).**
New admin-only, read-only endpoint `GET /api/admin/link-report`:
- Eran's open jobs (New + Approved) counted by link host: direct ATS, company domain, LinkedIn, Indeed, other boards.
- A dry run of the existing resolver on up to 50 of the board-linked jobs: how many resolve, to which ATS, and the title-match score.

Eran opens it once while signed in. **The result sets the target for "done" and decides how much of 1c is needed** — reported back before 1c starts.

**1b. Resolve when a job is saved (session 1).**
- New migration: `jobs.source_url` (where we found it), `jobs.link_type` (`direct` / `board` / `unknown`), `jobs.link_resolved_at`.
- `jobs.url` becomes the direct link when one is found; the LinkedIn URL moves to `source_url`, so nothing is lost.
- Resolution runs as a **worker job** after insert, not inside the search request, so a slow ATS never slows a search. One resolver call per company per run (cached), so 10 jobs at one company cost one lookup.
- Gemini slug guesses go through `gemini.py` and count against the existing daily ceilings.

**1c. Fallbacks for companies not on those five ATSes (session 2).**
In this order, stopping at the first verified hit:
1. **Workday.** The search loop already queries `myworkdayjobs.com` tenants; reuse that to match company + title.
2. **Known careers domains.** Small curated map (company → careers page/feed) seeded from the companies that actually appear in 1a's report — the same pattern as `_ATS_BOARD_MAP`.
3. **Grounded search, company domain only.** Ask Gemini for the posting **restricted to the company's own domain**; accept a URL only if `linkcheck` says it is alive **and** not a generic careers page **and** the page title matches the job title. This is the last resort because Gemini has invented URLs before (see the 10-12 STATUS entry).

Every accepted link is verified by `linkcheck.py` before it is stored. A link that fails verification is not stored.

**1d. When nothing is found (session 2).**
- Job keeps its LinkedIn link, `link_type='board'`.
- `/app` shows a **"No direct link"** badge, and the open button says "Open on LinkedIn" instead of "Open".
- Ranking: a small fixed penalty (**5 points, approved**), applied the same way `feedback_penalty` is (SQL ordering + payload + score explainer), so it is visible and explained, never hidden.
- Re-tried automatically once a week (postings often appear on the ATS a few days after LinkedIn).

**1e. Backfill existing open jobs (session 3).**
- One idempotent pass over Eran's New + Approved jobs (and any other user's), run as a worker job with a progress line in `/api/health`.
- Applied, passed and system-passed rows are not touched.

**1f. Visibility (session 3).**
- Admin page: share of open jobs with a direct link, by source; resolver hits by method (ATS / Workday / careers map / search).

**Done when:** the link report shows the direct-link share on new jobs at or above the target agreed after 1a; every unresolved job carries the badge and the penalty; the backfill has run; no applied/passed row changed (checked by test and by count before/after).

**Risks:** title matching across languages and seniority wording (mitigated by the existing threshold of 82 and the location tiebreak); LinkedIn changing its guest markup (existing risk, unchanged); Gemini cost (bounded by `gemini.py` ceilings).

### Step 2 — Link checker gaps (1 session)
The 10-12 rebuild caught 29 of 41 dead links. The 10 it still calls alive are aggregator pages (builtin, jobright, secrethunter) and invented company-site slugs that return the site's front page with HTTP 200.
- Aggregators: treat these hosts as **boards**, so they go through Step 1's resolver instead of being trusted as direct.
- Invented slugs: compare the landed page's title with the job title; the 10-12 attempt rejected a whole-page title rule because of false negatives on Payoneer, NICE and LinkedIn — so this time it applies **only to non-ATS company domains**, and the 41 observed URLs plus those pages become the test fixtures.

**Done when:** all 41 observed URLs classify correctly in `tests/test_linkcheck.py`, with no regressions on the live Payoneer/NICE/LinkedIn cases.

### Step 3 — Big-Tech sources (1 session)
Google (404), Apple (301 to a not-found page) and Microsoft fail; Amazon works.
- Re-discover each endpoint live from the workspace, fix the adapters, add a fixture test per adapter from a real response.
- Each adapter reports its last success and job count in `/api/health`, so the next silent break is visible.

**Done when:** all five Big-Tech adapters return Israel product roles in a live run and their health lines are green.

### Step 4 — Phase 5: plans, quotas, entitlements (2 sessions)
Builds on what exists: `users.plan` (migration 10) and `entitlements.py` (`plan_of`, `is_admin`, `can_auto_apply`).

**Session 1 — the machinery**
1. `plans.py`: one dict per plan (`free`, `beta`, `premium`, `expert`), quotas and feature flags, editable without a migration. Values: **the matrix Eran approves** (proposal in section 7). Step 4 does not start without that approval.
2. Migration: `usage_counters` (user, meter, period, count), `entitlement_overrides` (admin grants), `subscriptions` and `billing_events` (empty until Step 11, created now so Phase 6 adds no schema).
3. `entitlements.check(user, feature)` and `consume(user, meter, n)`, enforced **server-side** at every metered entry point: run-search, cover letter, CV optimizer, schedule frequency, notification channels, paid ingestion sources, apply.
4. An exhausted quota returns a clean `402` with a reason the UI can show — never a 500.
5. Everyone moves to `beta` (Premium-level quotas, free); Eran stays admin by role.

**Session 2 — what users and Eran see**
6. `/api/me` returns plan, features and live usage; `/app` shows meters, locked states and an upgrade hint (no prices — billing is dark).
7. Admin: set a user's plan, grant credits, per-user usage and estimated AI cost.

**Done when:** changing a plan in admin changes what the API allows on the next request; every gated path returns the clean paywall response when exhausted (one test per path); counters have recorded a week of real usage.

### Step 5 — Email verification + password reset (1 session)
**Blocker found while planning:** emails are sent from Resend's shared sandbox sender unless `RESEND_FROM` is set, and the sandbox can only deliver to the Resend account owner. Verification and reset mails to other users need a **verified sending domain**, i.e. the custom domain (Step 10's first half) must be bought and verified in Resend first.
- Signed, single-use, expiring tokens (stored hashed); verify-email gate on email/password signups (Google sign-ins are already verified by Google); "forgot password" flow that revokes other sessions, as password change already does.
- Rate-limited through `ratelimit.py`.

**Done when:** a new account on staging receives and completes verification; a reset link works once and then fails; both mails arrive at a non-Eran address.

### Step 6 — Legal + data controls (1 session)
- Draft Terms, Privacy Policy (naming Gemini, Resend, Twilio, Railway, Paddle, Sentry) and a cookie notice for Eran's review — drafts, not legal advice.
- `/settings` → **Export my data** (JSON + CV file) and **Delete my account** (cascades CV, jobs, sessions, push subscriptions; cancels the Paddle subscription once Step 11 exists).
- Retention policy written into the Privacy Policy and enforced by a scheduled purge.

**Done when:** export downloads a complete file; deletion removes every row for that user (checked across all tables by test) and they cannot sign in.

### Step 7 — Broad-exception audit (spread out, then 1 closing pass)
253 `except Exception` and 1 bare `except:` remain. Rule while doing Steps 1–6: every handler touched either narrows to the expected exception or logs it at WARNING/ERROR (which also reaches Sentry). Closing pass: a test that fails on new bare `except:` and on `except Exception: pass` with no log.

### Step 8 — Un-park auto-apply (P2, 2–3 sessions)
Follows Appendix A of `EXECUTION_PLAN_PUBLIC_LAUNCH.md`. Starting point is better than it was: the 10-07 self-test showed the runtime healthy (Chromium launches, Gemini key present, CV present), and Step 1 means far more jobs carry an ATS link the engine can actually submit to.
1. Fix the self-test's stale SQLite fields (old Step 14).
2. One live, supervised submit on Eran's own approved job, with `APPLY_ENGINE_ENABLED=1` set only for that run.
3. Measure success rate per ATS over Eran's real queue; only ATSes above an agreed rate stay enabled.
4. Apply-flow UX: clear manual-required state, retry visibility, confirmation evidence in the job detail.
5. The auto-apply toggle stays admin + paid only (already gated by `can_auto_apply`).

**Done when:** a measured success rate per ATS exists and Eran decides, from that number, whether auto-apply is something he would charge for.

### Steps 9–13 (P2/P3, one session each)
- **9 — Legacy HTML:** delete `DASHBOARD_HTML` and the other legacy pages (~3,000 lines) and the `LEGACY_UI=1` switch, after one last parity check against `PHASE4_UI_CONVERGENCE.md`.
- **10 — Domain + landing + funnel:** domain on Railway with HTTPS, Google OAuth redirect URI re-registered, public landing page, `robots.txt`, OG tags, funnel events (signup → CV → first search → first approve → paywall hit).
- **11 — Paddle sandbox:** products (Premium/Expert, monthly/annual, USD), checkout behind an admin flag, signed + idempotent webhooks, subscription state driving entitlements, in-app billing settings via Paddle's customer portal. `BILLING_LIVE=0` throughout.
- **12 — Feedback metrics:** are demoted jobs actually passed more, and approved ones applied more, than before? Shown on the admin page.
- **13 — Salary at pass:** store the job's salary when "Salary too low" is chosen and learn from it, as location already does.

---

## 5. What Eran needs to do (and when)

| When | Action | Why |
|---|---|---|
| Before Step 0 | Approve repo push access when prompted | Shipping from here |
| Step 0 | Create a free Sentry project; add `JH_SENTRY_DSN` to Railway | Error alerts |
| Step 1a | Open `/api/admin/link-report` once while signed in | Sets the link target |
| **Now — long lead time** | **Apply for a Paddle account** | Paddle reviews the business and product before approving; that can take days to weeks, and Step 11 needs it |
| Before Step 5 | **Choose and buy the domain**; add it in Resend for sending | Verification mail needs a verified sender |
| Step 6 | Review the legal drafts | Not legal advice |
| Step 8 | Be present for the first live submit | It sends a real application |

---

## 6. Open questions for Eran

1. **Tier matrix** (section 7) — Eran is refining it; needed before Step 4.
2. **Domain** — open; needed before Step 5.

---

## 7. Tier matrix

Lives in its own editable doc while Eran refines it: **Job Hunter — Plan tier matrix** (claude.ai doc, https://claude.ai/artifact/HNeqn25jQRU2taPiN3YrUm). Step 4 starts only after he approves it there; the approved version is copied into this section at that point.
