# Job-Hunter — Backlog

_Rewritten 2026-10-25; re-validated 2026-11-05; **rows 8, 12-15 updated 2026-11-08** (`/api/health` on commit `7c2c4eb` + live route probes).
The order, detail and "done when" of every open item live in **`EXECUTION_PLAN_NEXT.md`** — this file is the index._

Effort: **S** <½ day · **M** ~1–2 days · **L** multi-day.
Priority: **P0** security/correctness · **P1** reliability or launch-blocking · **P2** maintainability/launch polish · **P3** features.

## Open — in execution order

| Step | Item | Pri | Value | Effort | State (2026-11-05) |
|---|---|---|---|---|---|
| 1 | **Direct company links** — remaining: known careers-site map, agree the direct-link target | P1 | High | M | 1a/1b/1c (learned boards + Comeet + **Workday**)/1d shipped (#3–#5, #9, #12, #17); Gemini search tried, 0 of 18, not wired (#13, #15) |
| 2 | Link checker — remaining: nothing planned; the title-on-page rule was rejected on evidence | P1 | High | — | 2a/2b shipped (#7, #8); bot-challenge pages `unknown` (#15) |
| 4 | **Plans, quotas, entitlements (Phase 5)** — matrix approved 2026-11-05; 4a machinery, 4b enforcement + blurred matches, 4c UI + admin | P1 | High | L | **Done** - 4a (#19), 4b (#20), 4c (#21); history retention moves to Step 6 |
| 5 | Email verification + password reset — needs the custom domain | P1 | High | M | Not started (`/forgot-password` 404) |
| 6 | Legal pages + data export / account deletion + history retention (hide, not delete) | P1 | High | M | 6a export + delete (#22) and 6b history hiding (#23) shipped; legal drafts need the legal name + contact |
| 7 | Broad-exception audit | P1 | Med | M | **Done** (#24): 122 silent found, risky ones probed; Comeet removed (404), sync count fixed; ratchet test; 116 silent remain under the ratchet |
| 8 | Un-park auto-apply, incl. apply-flow UX | P2 | High | L | **Done except the switch** (#52-#61): first real submit 2026-11-08 (Payoneer); questions, security codes, notifications, per-system success rate; auto-apply is every user's choice. Remaining: Eran sets `APPLY_ENGINE_ENABLED=1` |
| 9 | Delete legacy HTML from `app.py` (Phase 4 step 5; closes most of old #13) | P2 | Med-High | M | **Done** (#25): app.py 9,062 → ~6,250 lines; old URLs redirect into /app |
| 10 | Custom domain + landing page + funnel analytics | P2 | Med | M | Not started (still on the Railway domain) |
| 11 | Payments, Paddle sandbox only (Phase 6) | P2 | Med | L | Not started; Paddle account status unrecorded |
| 12 | Feedback-loop effectiveness metrics (old #19) | P2 | Med | M | **Done** (Admin → Learning check) |
| 13 | Capture salary at pass time (old #17; location half done) | P3 | Low | M | **Done** (m0020; salary floor in the feedback penalty) |
| 14 | **Job-tailored CV** — rewrite the user's CV for one job description | P3 | Med | M | **Done** (2026-11-08): fact-checked against the CV, Word download; Free 2 / Premium 10 / Expert 30 a month |
| 15 | Lever hCaptcha: does it block real submits? | P2 | Med | S-M | Next after Step 1 (Eran's order, 2026-11-08) |

## Done since 2026-10-25
- **Step 0 — housekeeping** (#2, #6): PR shipping, `scripts/smoke.py`, Sentry on in production (`error_reporting: on`).
- **Step 3 — Big-Tech sources** (#11, #14): Google, Apple, Microsoft restored with per-source health; **Meta removed** (robots.txt prohibits automated collection; it had reported `ok, 0` every run).

## Parked

| Item | Why parked |
|---|---|
| Replace stdlib `http.server` (old #14) | Not worth it at current scale; the Postgres half is done |
| Android APK (Capacitor) | `/app` is already an installable PWA with web push; revisit with the Play Store |
| SecretJobs source | Adapter built; needs Eran's logged-in feed request + 3 Railway variables. **Its ingest hook `/api/sync/jobs` is unreachable key-only** (behind the session gate; relay.py never matched it) - redesign before un-parking (STATUS 2026-11-05) |

## Decisions of record (2026-10-25)
- Payments provider: **Paddle** (Stripe does not onboard Israeli entities without a US company).
- Every change ships as a PR, smoke-tested before merge (`scripts/smoke.py --boot-local`) and after (`--skip-local --expect-commit <sha>` against production).

## Shipped from the June audit
Secure session cookie (#1) · constant-time password check (#2) · login/register/search rate limits (#3) · notification
credentials encrypted at rest (#4) · CORS scoped (#5) · `.gitattributes` (#6) · `log.py` logging (#7) · apply-engine
tests (#9) · route + tenant-isolation tests (#10) · expired-session cleanup (#11) · blocklist wired (#12) · versioned
migrations (#15) · learned-preferences panel (#16) · feedback recency decay (#18).
