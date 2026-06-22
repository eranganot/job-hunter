# Job-Hunter — Backlog

_Generated from a codebase audit on 2026-06-09. There was no existing backlog, GitHub issue,
or TODO marker — this is derived from reading the code, tests, and architecture._

Effort key: **S** = <½ day · **M** = ~1–2 days · **L** = multi-day / needs design.

Priority key: **P0** correctness/security · **P1** reliability & observability · **P2** maintainability · **P3** features.

---

## P0 — Security & data integrity

| # | Item | Why it matters | Effort |
|---|------|----------------|--------|
| 1 | **Add `Secure` flag to the session cookie** (`auth.make_session_cookie`, line 186). Currently `HttpOnly; SameSite=Lax` only. | On Railway everything is HTTPS, so the cookie should never travel over plain HTTP. One-line fix. | S |
| 2 | **Constant-time password comparison.** `auth.verify_password` (line 34) uses `computed == stored_hash`. | Plain `==` is timing-attack-able. Swap to `hmac.compare_digest`. (Hashing itself is good: pbkdf2-sha256, 260k iters, per-user salt.) | S |
| 3 | **Login brute-force protection.** No rate limiting / lockout on the login route. | An exposed login with no throttle invites credential stuffing. Add per-IP/per-account attempt counting + backoff. | M |
| 4 | **Encrypt notification secrets at rest.** `telegram_token`, `twilio_auth_token`, `email_smtp_pass` are stored plaintext in SQLite (`db.py` 79–88). | A DB leak exposes users' messaging/email creds. Encrypt with a key from env, or move to a secrets store. | M |
| 5 | **Tighten CORS.** `Access-Control-Allow-Origin: *` is sent on responses (app.py 4910, 4984). | Sloppy even though cookies aren't sent to wildcard origins. Scope it to the known frontend origin. | S |
| 6 | **Add `.gitattributes` for line-ending normalization.** The repo mixes LF and CRLF (`ai_analysis.py` is CRLF, others LF), which caused a corrupted/garbled working copy during recent edits. | Prevents whole-file CRLF/LF diff churn and the file-corruption class of problem. `* text=auto eol=lf`. | S |

## P1 — Reliability, observability & testing

| # | Item | Why it matters | Effort |
|---|------|----------------|--------|
| 7 | **Replace `print()` logging with the `logging` module.** ~166 `print()` calls across the codebase (116 in app.py). | No levels, no timestamps, no structured output — hard to debug prod issues on Railway. Introduce a logger with INFO/WARN/ERROR. | M |
| 8 | **Audit the 116 broad `except Exception` + 4 bare `except:`.** Many silently swallow errors (e.g. the sync-jobs insert loop at line 6047 `except: pass`). | Swallowed exceptions hide real failures (dead links, partial inserts, apply errors). At minimum log them; narrow where possible. | M |
| 9 | **Test coverage for `apply_engine.py` (802 lines, zero dedicated tests).** This is the most-patched area — 7 of the 8 stale branches are `fix/apply-*`. | The application-submission flow is the most fragile and least covered part of the app. Add unit/integration tests for retry/backoff, stuck-`applying` recovery, and manual-required paths. | L |
| 10 | **Route-level tests for `app.py`.** The HTTP handler (6180 lines) has no tests; only `ai_analysis`, `db`, and the TLV parser are covered. | Regressions in auth, job actions, and scoring endpoints ship undetected. Add a test harness that exercises key routes. | L |
| 11 | **Expired-session cleanup job.** Sessions are only deleted on logout (`auth.py` 148); expired rows accumulate forever. | Table bloat + stale rows. Add a periodic `DELETE FROM sessions WHERE expires_date < datetime('now')`. | S |
| 12 | **Dead-code cleanup: `user_blocklist`.** `add_to_blocklist` / `get_blocklist` exist (db.py) but are **never called** anywhere. | Either wire it up (see #16) or remove it so the schema reflects reality. | S |

## P2 — Architecture & maintainability

| # | Item | Why it matters | Effort |
|---|------|----------------|--------|
| 13 | **Break up the 6180-line `app.py`.** It bundles the HTTP handler, all routes, business logic, and ~384 inline HTML/CSS/JS elements as Python strings. | Single biggest maintainability drag. Split into modules (routes, services) and move the frontend out of Python string literals into template/static files. | L |
| 14 | **Reconsider the stdlib `http.server` + threaded SQLite stack at scale.** `BaseHTTPRequestHandler` + `ThreadingMixIn` + a single WAL SQLite file. | Fine for a few users; SQLite serializes writes and the hand-rolled server lacks graceful concurrency/timeouts. If multi-user grows, plan a move to a real WSGI/ASGI app and/or Postgres. | L |
| 15 | **Centralize DB migrations.** Migrations are an inline `try/except pass` list in `init_db` (db.py 121+). | Works, but silently ignores all errors and has no versioning. A lightweight migration runner with explicit versions would be safer. | M |

## P3 — Features (incl. extending the new feedback-learning loop)

| # | Item | Why it matters | Effort |
|---|------|----------------|--------|
| 16 | **Surface & manage learned preferences in the UI.** Add a settings panel showing flagged "bad" companies, demoted patterns, and pass-reason stats, with the ability to undo. Wire the existing `user_blocklist` here. | The new learning loop is invisible today except the ⬇ badge. Users should see and correct what the app learned. | M |
| 17 | **Capture salary & location at pass time so they can be learned deterministically.** Right now "Salary too low" / "Wrong location" only influence the AI-context half — the actual values aren't stored on the job, so rules can't act on them. | Store job salary/location and add deterministic penalties for them, closing the loop fully. | M |
| 18 | **Recency-weight / decay old feedback.** A company passed once a year ago currently penalizes forever. | Preferences drift; weight recent passes more and let stale signals fade. | M |
| 19 | **Feedback effectiveness metrics.** Track whether demoted jobs actually get passed less / approved jobs get applied more over time. | Validates that the learning loop helps rather than just reshuffles. | M |
| 20 | **Apply-flow UX hardening** (carryover theme from the `fix/apply-*` branches): clearer manual-required states, retry visibility, and confirmation reliability. | The apply pipeline is where most past bugs lived; user-facing clarity reduces confusion when auto-apply can't complete. | M |

---

### Quick wins to do first
Items **1, 2, 5, 6, 11, 12** are all **S** and mostly independent — a good first cleanup pass that meaningfully improves security and repo hygiene in well under a day.

### Notes
- All 8 non-`main` branches are already merged (0 commits ahead) and can be deleted to declutter.
- Uncommitted working-tree changes exist in config/test files (`relay.py`, `requirements.txt`, `Dockerfile`, `tests/*`, etc.) — review and commit or discard.
