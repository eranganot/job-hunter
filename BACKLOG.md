# Job-Hunter — Backlog

_Rewritten 2026-10-25; re-validated 2026-11-05; **re-prioritised 2026-11-08 (board-first search next)** (`/api/health` on commit `7c2c4eb` + live route probes).
The order, detail and "done when" of every open item live in **`EXECUTION_PLAN_NEXT.md`** — this file is the index._

Effort: **S** <½ day · **M** ~1–2 days · **L** multi-day.
Priority: **P0** security/correctness · **P1** reliability or launch-blocking · **P2** maintainability/launch polish · **P3** features.

## Next — in execution order (re-prioritised 2026-11-08 with Eran)

| # | Step | Item | Pri | Value | Effort | State (2026-11-08) |
|---|---|---|---|---|---|---|
| 1 | 16 | **Board-first search** (Step 1, part 2): search the job boards the app already knows (Comeet, Greenhouse, Lever, Ashby, SmartRecruiters, Workday, plus every board the careers-map learns) for the user's titles, so new jobs arrive with a direct link instead of being fixed later. Includes **resolve-before-show**: run the link lookups at intake, so a job reaches the swipe deck already direct where possible | P1 | High | M-L | Not started. Why: the main search brings ~45% board/aggregator links (by_source, 2026-11-07) and the share of open jobs follows the share of incoming ones |
| 2 | 1 | **Direct-link target**: decide after one week of board-first data. Today 58.1%; with the current tools ~65% (measured hit rates); 70% needs #1; **80%+** also needs the LinkedIn-only rule (#3) | P1 | High | S | Waiting on #1's data |
| 3 | 17 | **LinkedIn-only jobs that cannot be resolved:** keep in the queue (as today, badge + 5-point penalty), move to a separate "Apply on LinkedIn" list not counted in the share and never auto-applied, or drop. **Eran decides with #1's numbers**; recommendation: a separate list | P1 | High | S-M | Decision pending |
| 4 | 15 | Lever hCaptcha: does it block real submits? | P2 | Med | S-M | After #1 (Eran's order, 2026-11-08) |

## Waiting on Eran

| Step | Item | Unblocks |
|---|---|---|
| 8 | Set `APPLY_ENGINE_ENABLED=1` on Railway (`web`) | Real auto-apply; per-system success rates (Admin → Apply results by system) |
| 5, 10 | **Buy the domain**, add it to Resend | Email verification + password reset (`/forgot-password` 404), landing page + funnel, email notifications to anyone but Eran |
| 11 | Paddle account approval | Payments (sandbox first) |
| 6 | Legal placeholders: database region, Sentry region, log retention, liability cap, publication date, paid plans | Publishing Terms / Privacy |

## Steps — status

| Step | Item | Pri | State (2026-11-08) |
|---|---|---|---|
| 1 | Direct company links | P1 | 1a-1d, Workday, web search after each search (#30s), **careers-map** (#68: 2 of 30 hard jobs resolved free). Remaining: #1-#3 above |
| 2 | Link checker | P1 | **Done** (2a/2b, #7, #8) |
| 4 | Plans, quotas, entitlements (Phase 5) | P1 | **Done** (#19-#21) |
| 5 | Email verification + password reset | P1 | Blocked: domain |
| 6 | Legal + data export / deletion + history hiding | P1 | 6a/6b shipped (#22, #23); legal drafts need the placeholders above |
| 7 | Broad-exception audit | P1 | **Done** (#24); ratchet keeps it |
| 8 | Un-park auto-apply + apply-flow UX | P2 | **Done except the switch** (#52-#61, #67): first real submit 2026-11-08; questions, security codes, notifications, per-system rates; every user's choice |
| 9 | Delete legacy HTML | P2 | **Done** (#25) |
| 10 | Domain + landing + funnel | P2 | Blocked: domain |
| 11 | Payments (Paddle sandbox) | P2 | Blocked: Paddle account |
| 12 | Feedback-loop metrics | P2 | **Done** (Admin → Learning check) |
| 13 | Salary at pass time | P3 | **Done** (m0020) |
| 14 | Job-tailored CV | P3 | **Done** (#62-#66): fact-checked, every CV role guaranteed, editable, user picks which CV is sent; cover letters from the full CV (#63) |

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
