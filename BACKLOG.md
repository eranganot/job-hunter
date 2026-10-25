# Job-Hunter — Backlog

_Rewritten 2026-10-25. Replaces the June 2026 codebase audit, 13 of whose 20 items have shipped (listed at the end).
The order, detail and "done when" of every open item live in **`EXECUTION_PLAN_NEXT.md`** — this file is the index._

Effort: **S** <½ day · **M** ~1–2 days · **L** multi-day.
Priority: **P0** security/correctness · **P1** reliability or launch-blocking · **P2** maintainability/launch polish · **P3** features.

## Open — in execution order

| Step | Item | Pri | Value | Effort |
|---|---|---|---|---|
| 0 | Housekeeping: push from the workspace, Mac-era CLAUDE.md, `scripts/smoke.py`, Sentry on | P1 | High | S |
| 1 | **Direct company links** — every job links to the company's own posting, not LinkedIn/Indeed | P1 | High | L |
| 2 | Link checker: aggregator pages and invented slugs still read as alive | P1 | High | M |
| 3 | Big-Tech sources: Google, Apple, Microsoft adapters broken | P1 | Med | S–M |
| 4 | **Plans, quotas, entitlements (Phase 5)** — waits for the approved tier matrix | P1 | High | L |
| 5 | Email verification + password reset — needs the custom domain | P1 | High | M |
| 6 | Legal pages + data export / account deletion | P1 | High | M |
| 7 | Broad-exception audit (253 `except Exception`, 1 bare `except:`) | P1 | Med | M |
| 8 | Un-park auto-apply, incl. apply-flow UX and the self-test's stale SQLite fields | P2 | High | L |
| 9 | Delete legacy HTML from `app.py` (Phase 4 step 5; closes most of old #13) | P2 | Med-High | M |
| 10 | Custom domain + landing page + funnel analytics | P2 | Med | M |
| 11 | Payments, Paddle sandbox only (Phase 6) | P2 | Med | L |
| 12 | Feedback-loop effectiveness metrics (old #19) | P2 | Med | M |
| 13 | Capture salary at pass time (old #17; location half done) | P3 | Low | M |

## Parked

| Item | Why parked |
|---|---|
| Replace stdlib `http.server` (old #14) | Not worth it at current scale; the Postgres half is done |
| Android APK (Capacitor) | `/app` is already an installable PWA with web push; revisit with the Play Store |
| SecretJobs source | Adapter built; needs Eran's logged-in feed request + 3 Railway variables |

## Decisions of record (2026-10-25)
- Payments provider: **Paddle** (Stripe does not onboard Israeli entities without a US company).
- Every change ships as a PR, smoke-tested before merge (`scripts/smoke.py --boot-local`) and after (`--skip-local --expect-commit <sha>` against production).

## Shipped from the June audit
Secure session cookie (#1) · constant-time password check (#2) · login/register/search rate limits (#3) · notification
credentials encrypted at rest (#4) · CORS scoped (#5) · `.gitattributes` (#6) · `log.py` logging (#7) · apply-engine
tests (#9) · route + tenant-isolation tests (#10) · expired-session cleanup (#11) · blocklist wired (#12) · versioned
migrations (#15) · learned-preferences panel (#16) · feedback recency decay (#18).
