# Phase 4 — UI convergence: the verified inventory

_2026-09-15. Every row below was checked against the code, not taken from the
plan. Three of the plan's rows were wrong; they are corrected here._

Method: extracted every `/api/...` string from each legacy HTML constant in
`app.py` and from `web/src/` (`client.ts` + `App.tsx`), including the
`${id}`-templated ones, and diffed the two sets.

---

## What the legacy UI actually is

`app.py` lines 2475–5380 — **2,905 lines**, six HTML string constants, styled by
a frozen gzip+base64 Tailwind blob at `app.py:33` that no build step regenerates:

| Constant | Lines | What it is |
|---|---:|---|
| `LOGIN_HTML` | 52 | Sign in (**shared** — both UIs use it) |
| `REGISTER_HTML` | 67 | Create account (**shared**) |
| `ONBOARDING_HTML` | 804 | Upload CV → profile → schedule → notifications |
| `SETTINGS_HTML` | 750 | Profile, notification channel, schedule, admin, password |
| `ADMIN_HTML` | 162 | Queue overview, maintenance, users |
| `DASHBOARD_HTML` | 1,070 | The legacy job queue |

---

## The real gap

### Genuinely missing from `/app` — must be built

| Capability | Endpoint | Why it matters |
|---|---|---|
| **CV → profile extraction** | `/api/analyze-cv` | **The one that blocks public launch.** In `/app` a new user uploads a CV and nothing reads it — they must type every job title, keyword and location by hand. Legacy onboarding does this automatically. Distinct from `/api/cv-optimizer-analyze`, which *scores* a CV; this one *populates the profile from it*. |
| **Notification channels** | `/api/save-notifications` (channel fields), `/api/test-notification` | `/app` offers web push only. Telegram, WhatsApp/Twilio and email SMTP cannot be configured from `/app` at all — and those are exactly the three fields `crypto.py` encrypts, so the encryption work has no UI reaching it. |
| **Change password** | `/api/change-password` | Absent from `/app`. Now also revokes the user's other sessions (2026-09-15), which is worth surfacing in the UI rather than leaving silent. |
| **Onboarding flow** | `/api/dismiss-onboarding` + the four steps | A new public signup lands on a page that looks nothing like the product. |
| **Pipeline stages** | `/api/set-stage` | Screening / Interviewing / Offer / Rejected — the entire post-application workflow. |
| **Bulk select + actions** | `/api/jobs/bulk` | |
| **Manual apply trigger** | `/api/run-apply` | Now queued and daily-capped (2026-09-15). |
| **Cover letter** | `/api/jobs/<id>/cover-letter` | Admin-gated today. |
| **Job status re-check** | `/api/jobs/<id>/check-status` | |
| **Enable/disable a user** | `/api/admin/users/<id>/toggle` | Admin panel has the user list but not the toggle. |

### Corrections to the plan's gap table

1. **"Admin apply probes — port from legacy" is wrong.**
   `/api/admin/apply-selftest`, `/api/admin/apply-test` and `/api/admin/inject-jobs`
   have **no UI anywhere** — not in `ADMIN_HTML` either. They are curl-only
   endpoints. Building them is *new* UI, not a port. Worth doing for
   `apply-selftest`: it is the 8-week-old ops item that has never been run
   because running it means hand-crafting a request.

2. **"Admin panel — port" overstates it.** Every endpoint `ADMIN_HTML` calls is
   **already wired in `/app`'s `AdminModal`**: queue-stats, users,
   clear-attempted, clear-applied, rescore, dedup. The admin work is a
   *redesign* plus the missing user toggle — not a port.

3. **`/api/jobs/<id>/retry` is not a gap.** `/app`'s "Retry application" button
   calls `apply-now`, which is the same outcome by a different route.

### Endpoints with no UI in either place
`/api/admin/apply-selftest`, `/api/admin/apply-test`, `/api/admin/inject-jobs`,
`/api/push/unsubscribe`.

---

## Proposed sequence

Ordered by what blocks a public paid launch, not by size.

| # | Piece | Size | Status | Why here |
|---:|---|---|---|---|
| 1 | **Settings: channels, password, CV analysis** | M | **DONE** (`a2574df`…`1aa0f4d`) | Closes the encryption loop, and `analyze-cv` is the dependency onboarding needs anyway. |
| 2 | **Onboarding** | L | **DONE** | First thing a new signup sees; the whole point of Phase 5+. Reuses everything from (1). |
| 3 | **Pipeline stages + bulk actions** | M | **DONE** — and both endpoints were broken | The power features that make `/dashboard` still worth opening. |
| 4 | **Admin redesign + user toggle + apply probes** | M | **DONE** — `apply-test` live deliberately left out | Once nothing else needs the legacy UI. |
| 5 | **Restyle login / register / Google button** | S | **DONE** — and now self-contained | Shared pages; the seam a new user sees first. |
| 6 | **Flip `/dashboard` → `/app`, then delete** | S | **NOT STARTED** | ~2,900 lines out of `app.py`. Behind `LEGACY_UI=1` for one release. |

### Verified remaining, 2026-09-15 (checked against the code, not this table)

Grepped `web/src/` for every endpoint in the gap table above. `/api/set-stage`
and `/api/jobs/bulk` are now wired (2026-09-15). **Still zero references** to
`/api/jobs/<id>/cover-letter`, `/api/jobs/<id>/check-status`,
`/api/admin/users/<id>/toggle` and the three admin apply probes — those are the
real remainder, alongside items 5 and 6.

**One loose end of my own:** `api.runApply` exists in `client.ts` and is called
from nothing (`grep -c 'api.runApply' App.tsx` → 0). Either wire the button or
delete the function; a dead API wrapper reads as a shipped feature to the next
person looking.

**The flip (6) is three hardcoded destinations, not a refactor:** `dest =
"/dashboard"` on login POST (`app.py:6622`), and `/login` + `/register` GET
redirecting an already-authenticated user to `/dashboard` (`:5801`, `:5809`).
`/register` POST already sends new accounts to `/app`. Non-admins hitting
`/admin` also land on `/dashboard` (`:5959`).

**Settings gained more than item 1 asked for**, from Eran's hands-on rounds:
four tabs with Account merged into Profile, full name + LinkedIn URL, schedule
frequency with Monday-first day pickers for search AND apply, multi-select
notification channels, per-field password reveal, an auto-apply toggle behind
`entitlements.can_auto_apply()`, and `?onboarding=1` to replay setup.

Design language is settled and not up for redesign: the dark palette already in
`/app`, the `max-w-[1600px]` shell, the `lg:` sidebar, and the card grid at
`xl:grid-cols-2 2xl:grid-cols-3`. "Align with the new design" means match that.

**Nothing is deleted until (6)**, and (6) keeps an escape hatch for one release.
