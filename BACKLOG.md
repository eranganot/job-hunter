# Job-Hunter — Backlog

_Rewritten 2026-10-25; re-validated 2026-11-05; **re-prioritised 2026-11-08 (board-first search next)** (`/api/health` on commit `7c2c4eb` + live route probes).
The order, detail and "done when" of every open item live in **`EXECUTION_PLAN_NEXT.md`** — this file is the index._

Effort: **S** <½ day · **M** ~1–2 days · **L** multi-day.
Priority: **P0** security/correctness · **P1** reliability or launch-blocking · **P2** maintainability/launch polish · **P3** features.

## Next — in execution order (re-prioritised 2026-11-08 with the owner)

| # | Step | Item | Pri | Value | Effort | State (2026-11-08) |
|---|---|---|---|---|---|---|
| 1 | 16 | **Board-first search** (Step 1, part 2): search the job boards the app already knows (Comeet, Greenhouse, Lever, Ashby, SmartRecruiters, Workday, plus every board the careers-map learns) for the user's titles, so new jobs arrive with a direct link instead of being fixed later. Includes **resolve-before-show**: run the link lookups at intake, so a job reaches the swipe deck already direct where possible | P1 | High | M-L | **Done** (#71, 2026-11-08); source labels fixed #126 |
| 2 | 1 | **Direct-link target** | P1 | High | S | **Decided 2026-11-15: ~75%, good enough for now** (71.9% of open jobs direct after week 1, from 58.1%). Improving it further is tech debt, below |
| 3 | 17 | **LinkedIn-only jobs that cannot be resolved:** keep in the queue as today (badge + 5-point penalty) | P1 | High | S | **Decided 2026-11-15: keep as now** |
| 4 | 15 | Lever hCaptcha: does it block real submits? | P2 | Med | S-M | After #1 (The owner's order, 2026-11-08) |

## Waiting on the owner

| Step | Item | Unblocks |
|---|---|---|
| 8 | Set `APPLY_ENGINE_ENABLED=1` on Railway (`web`) | Real auto-apply; per-system success rates (Admin → Apply results by system) |
| 5, 10 | **Buy the domain**, add it to Resend | Email verification + password reset (`/forgot-password` 404), landing page + funnel, email notifications to anyone but the owner |
| 11 | Paddle account approval | Payments (sandbox first) |

## Steps — status

| Step | Item | Pri | State (2026-11-08) |
|---|---|---|---|
| 1 | Direct company links | P1 | 1a-1d, Workday, web search after each search (#30s), **careers-map** (#68: 2 of 30 hard jobs resolved free). Remaining: #1-#3 above |
| 2 | Link checker | P1 | **Done** (2a/2b, #7, #8) |
| 4 | Plans, quotas, entitlements (Phase 5) | P1 | **Done** (#19-#21) |
| 5 | Email verification + password reset | P1 | **Done** (#91); left: confirm both mails reach a non-admin inbox |
| 6 | Legal + data export / deletion + history hiding | P1 | **Done** (#22, #23, Terms/Privacy 2026-11-11); lawyer review before paid plans |
| 7 | Broad-exception audit | P1 | **Done** (#24); ratchet keeps it |
| 8 | Un-park auto-apply + apply-flow UX | P2 | **Done except the switch** (#52-#61, #67): first real submit 2026-11-08; questions, security codes, notifications, per-system rates; every user's choice |
| 9 | Delete legacy HTML | P2 | **Done** (#25) |
| 10 | Domain + landing + funnel | P2 | **Done** (job-hunter.work on Cloudflare + Railway, landing + funnel this PR) |
| 11 | Payments (Paddle sandbox) | P2 | Blocked: Paddle account |
| 12 | Feedback-loop metrics | P2 | **Done** (Admin → Learning check) |
| 13 | Salary at pass time | P3 | **Done** (m0020) |
| 14 | Job-tailored CV | P3 | **Done** (#62-#66): fact-checked, every CV role guaranteed, editable, user picks which CV is sent; cover letters from the full CV (#63) |

## Done since 2026-10-25
- **Step 0 — housekeeping** (#2, #6): PR shipping, `scripts/smoke.py`, Sentry on in production (`error_reporting: on`).
- **Step 3 — Big-Tech sources** (#11, #14): Google, Apple, Microsoft restored with per-source health; **Meta removed** (robots.txt prohibits automated collection; it had reported `ok, 0` every run).

## Tech debt

| Item | Why it matters | Where to start |
|---|---|---|
| **Raise the direct-link share past ~75%** (owner, 2026-11-15: good enough for now, improve later) | Every job without a direct link is one the apply engine cannot submit and the user has to chase on LinkedIn. Week 1: 71.9% of open jobs direct; the remaining ~28% are mostly LinkedIn-only listings from small Israeli companies (498 of 581 non-direct) | (1) Read the now-true by-source split (#126) after a week to see which source brings the non-direct jobs. (2) Grow the board list the board-first search knows (learned companies: 172). (3) Better company-name matching in the resolver - its dry run hit 1 of 36 (2.8%), with 14 not reached in time. (4) Unresolvable LinkedIn-only jobs stay in the queue (owner, 2026-11-15) |

## New ideas — not scheduled (owner, 2026-11-15)

Not prioritised yet. **LinkedIn limits, checked 2026-11-15:** an outside app gets only name, headline, photo and email through "Sign in with LinkedIn" (OpenID Connect). Work history, skills and connections need LinkedIn partner approval, which a small app should not plan on. Scraping LinkedIn breaks its terms and risks the user's account; the owner already decided against a LinkedIn scraper (2026-11-09). So everything below uses the member's own LinkedIn data export (Settings → Data privacy → Get a copy of your data) or deep links that the user opens on LinkedIn.

| # | Idea | What we can build | Limits and open questions |
|---|---|---|---|
| A | **Connect the profile to the user's LinkedIn** | "Sign in with LinkedIn" next to Google; import work history and skills from the LinkedIn export (`Positions.csv`, `Skills.csv`, `Profile.csv`) to fill the Profile page and feed CV tailoring and scoring | No live sync: the user re-uploads the export to refresh. Privacy Policy gains LinkedIn as a sign-in provider |
| B | **People you know at each job's company** | Import `Connections.csv` (1st-degree: name, profile link, current company, title, connected-on). Match to every job's company: "You know 2 people at Finout". Show it on the job card, add it to the score, and offer a "Draft a referral message" button. For **2nd-degree**: a "Find people I know at <company>" button that opens LinkedIn's own search filtered to that company and the user's 1st and 2nd connections | 2nd-degree cannot be automated (not in the export, no permitted API). Company names need matching across spellings (the resolver's matching helps). The export goes stale. **Connections' details are third-party personal data**: kept private to the user, never shown to others, listed in the Privacy Policy, deleted with the account |
| C | **Community: users help each other with referrals** (first idea, the owner is developing it) | Users opt in to being a referrer at their company. A job seeker sees "a Job Hunter member works at this company and refers" on matching jobs and can send a referral request through the app. Optional feed of open roles at members' companies, and of requests waiting for you | To define with the owner: what a referrer sees about a candidate and what the candidate shares (consent, both sides opt in); abuse and spam limits; whether referral bonuses matter; the cold start (works only with members at many companies, so it needs the user base first); Terms and Privacy updates (data shared between users) |

## Parked

| Item | Why parked |
|---|---|
| Replace stdlib `http.server` (old #14) | Not worth it at current scale; the Postgres half is done |
| Android APK (Capacitor) | `/app` is already an installable PWA with web push; revisit with the Play Store |
| SecretJobs source | Adapter built; needs the owner's logged-in feed request + 3 Railway variables. **Its ingest hook `/api/sync/jobs` is unreachable key-only** (behind the session gate; relay.py never matched it) - redesign before un-parking (STATUS 2026-11-05) |

## Decisions of record (2026-10-25)
- Payments provider: **Paddle** (Stripe does not onboard Israeli entities without a US company).
- Every change ships as a PR, smoke-tested before merge (`scripts/smoke.py --boot-local`) and after (`--skip-local --expect-commit <sha>` against production).

## Shipped from the June audit
Secure session cookie (#1) · constant-time password check (#2) · login/register/search rate limits (#3) · notification
credentials encrypted at rest (#4) · CORS scoped (#5) · `.gitattributes` (#6) · `log.py` logging (#7) · apply-engine
tests (#9) · route + tenant-isolation tests (#10) · expired-session cleanup (#11) · blocklist wired (#12) · versioned
migrations (#15) · learned-preferences panel (#16) · feedback recency decay (#18).
