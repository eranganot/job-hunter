# Railway runbook — the Postgres cutover and the LLM ceiling

_Written 2026-09-20. Two jobs that both happen in the Railway console, and only
one of them is dangerous._

Both are **Eran-only**: they touch production variables and production data, and
neither can be done from a chat session. Everything I can do from here is already
in the repo — the migration script, the preflight guard, the smoke assertions.

---

## Part 1 — The LLM ceiling (do this one first; it is 5 minutes and reversible)

### Why it has been open for a week

`JH_LLM_GLOBAL_CALLS=50000` is a placeholder. It shipped generous on purpose,
because before migration 7 nothing in the app had ever counted a Gemini call, so
there was no number to set it to. STATUS has carried "set it from the real
number" ever since — and could not close it, because `/api/health` only ever
reported **today's** spend. One day is not a peak.

That is fixed in the build you are about to deploy: `/api/health` now carries
`llm_history`, fourteen days of per-day totals, newest first.

### Step 1.1 — Deploy first, then read

The history does not exist on the box until this build is on it. So:

```powershell
.\scripts\ship.ps1
.\scripts\smoke.ps1
```

### Step 1.2 — Read the real numbers

```powershell
$h = Invoke-RestMethod https://web-production-192b7.up.railway.app/api/health
$h.llm_history | Format-Table day, calls, tokens, users, max_user_calls
$peak = ($h.llm_history | Measure-Object calls -Maximum).Maximum
"peak day: $peak calls"
```

Expect it to be small. Production has 9 users and the searches are scheduled,
not interactive — if the peak comes back under a few hundred, that is the
correct answer, not a broken counter. A day with `calls: 0` is a day nothing was
scheduled.

### Step 1.3 — Set the variable

Railway → project **Job-Hunter** → environment **production** → service **web** →
**Variables**.

| Variable | Set it to | Why that number |
|---|---|---|
| `JH_LLM_GLOBAL_CALLS` | **10× the peak day**, rounded up to something memorable | Headroom for a bad day and for the users you are about to add, while still being a ceiling a runaway loop hits in minutes instead of never. 50,000 is not a ceiling at 9 users; it is a decoration. |
| `JH_LLM_USER_CALLS` | **10× the busiest single account's day** | Bounds one account's blast radius. Each `llm_history` row carries `max_user_calls` — the busiest account that day — and `smoke.ps1` prints it and fails if the ceiling is at or below a real observed day. |

Leave `JH_LLM_ENFORCE=1`. Setting it to `0` counts and alerts without blocking —
useful if you ever want a week of observation before a ceiling bites, but you
have that week now.

**No deploy needed.** `gemini.py` reads the limits per call. The variable takes
effect on the next Gemini request. Railway will restart the container anyway on a
variable change; that is fine, the ledger is in the database.

### Step 1.4 — Confirm

```powershell
(Invoke-RestMethod https://web-production-192b7.up.railway.app/api/health).llm.limits
```

Rollback is setting the number back. There is nothing else to undo.

---

## Part 2 — The Postgres cutover (this is the dangerous one)

### What went wrong last time, so you know what the steps are defending against

On **2026-09-07** production was flipped by setting `DB_BACKEND=postgres` alone.
Railway had auto-injected a `DATABASE_URL` pointing at its default `railway`
database. The app took it, ran its own migrations there, and served nine users a
completely empty account — while `/api/health` reported `"ok"`, because an empty
database is a perfectly healthy database.

`db.preflight()` now refuses exactly that, before `init_db()` runs, on two
grounds:

1. **The database must be named on purpose.** `JH_PG_DATABASE` must be set and
   must equal the database in `DATABASE_URL`. `DB_BACKEND=postgres` on its own no
   longer selects anything.
2. **An empty Postgres must never displace a populated SQLite volume.** Zero
   users there and nine here is a cutover that has not happened yet.

A refusal falls back to SQLite, prints a banner in the deploy logs, and shows up
at `/api/health` as `db_backend_refused`. `scripts/smoke.ps1` fails on it.

**So the guard has your back — but it is a guard, not a plan.** The plan is
below, and its shape is: *migrate the data first, verify it, then set all three
variables together.*

### Step 2.0 — Preconditions

- [ ] Part 1 done and the current build deployed and smoking green.
- [ ] Staging has been on Postgres since 2026-09-07 and is healthy. (It has.)
- [ ] You have the Railway CLI linked: `railway status` names the project.
- [ ] **Pick a quiet window.** The scheduler runs daily searches; a cutover
      mid-run means the run finishes against SQLite and the next one starts
      against Postgres. Not fatal, but avoidable.

### Step 2.1 — Back up production, and verify the backup opens

```powershell
.\scripts\railway_phase0.ps1 -Backup
```

It pulls `jobs.db` off the production volume into `C:\dev\_backups\job-hunter\`
and verifies it opens and counts rows, because a backup nobody has opened is a
file, not a backup. **Do not continue until it reports a user count you
recognise (9).**

### Step 2.2 — Create the target database

The production database must be a **different name** from staging's. Staging is
`jobhunter_staging`; production is `jobhunter_prod`. From the repo root, in
PowerShell:

```powershell
railway run --service Postgres python scripts/pg_create_db.py jobhunter_prod
```

Expect `[OK] created database jobhunter_prod` and a list of the databases on the
server that includes it. Running it twice is harmless — the second run says the
database already exists and leaves it alone. It only ever creates.

_(This step first said `railway connect Postgres` and then to type SQL "at the
psql prompt". `railway connect` needs the psql client installed locally; without
it the command exits straight back to PowerShell, which then tries to run the SQL
as a PowerShell command. `pg_create_db.py` uses psycopg, which the migration
script in 2.3 already needs, so nothing new has to be installed.)_

### Step 2.3 — Migrate the data

From your machine, with the backup you just verified:

```powershell
# 1. dry run — reads everything, writes nothing, reports what it would copy
railway run --service Postgres python scripts/sqlite_to_pg.py `
    C:\dev\_backups\job-hunter\jobs.db --database jobhunter_prod --dry-run --allow-prod

# 2. the real copy
railway run --service Postgres python scripts/sqlite_to_pg.py `
    C:\dev\_backups\job-hunter\jobs.db --database jobhunter_prod --allow-prod
```

`--allow-prod` is required because the script refuses a target whose name
contains *prod* unless you say you mean it. That refusal exists for the same
reason everything else here does.

The script renders the schema through `migrations.ddl_for()`, so the Postgres
side gets the same schema version the app expects (**11**), then copies
table-by-table and resets the sequences. Sequences matter: without the reset the
first insert collides with an existing id.

### Step 2.4 — Verify before you point anything at it

```powershell
railway run --service Postgres python scripts/sqlite_to_pg.py `
    C:\dev\_backups\job-hunter\jobs.db --database jobhunter_prod --verify-only --allow-prod
```

This skips the copy and runs row counts **and value-by-value checksums**. Row
counts alone will happily agree about a table whose contents got mangled in the
type conversion.

**Expect: 9 users, ~2,600 jobs, all tables matching.** If anything disagrees,
stop — the SQLite volume is still what production is serving, so nothing is lost
and there is nothing to roll back.

### Step 2.5 — Set the three variables TOGETHER

Railway → **production** → **web** → **Variables**. Railway applies a batch of
variable edits as one redeploy, so use the "Raw editor" / multi-edit rather than
saving them one at a time. **Setting `DB_BACKEND` alone is the 2026-09-07
incident.**

| Variable | Value |
|---|---|
| `DB_BACKEND` | `postgres` |
| `DATABASE_URL` | the connection string for **`jobhunter_prod`** — not the injected default. Take Railway's Postgres URL and replace the trailing database name with `jobhunter_prod`. |
| `JH_PG_DATABASE` | `jobhunter_prod` |

Leave `DATABASE_PATH` and the volume alone. The SQLite file stays on the volume,
untouched, as your instant rollback.

### Step 2.6 — Watch the deploy logs

You are looking for the **absence** of this banner:

```
==============================================================================
REFUSING THE CONFIGURED POSTGRES TARGET
  ...
  Serving the SQLite volume instead. Fix the variables and redeploy.
==============================================================================
```

If it appears, read the reason — it names which of the two checks failed and what
to set. The app is serving SQLite in the meantime, so you have time.

### Step 2.7 — Smoke it, pinned

```powershell
.\scripts\smoke.ps1 -ExpectBackend postgres
```

`-ExpectBackend postgres` is the point of this step. Without it, a silent
fallback to SQLite **passes** the smoke — which is precisely how a cutover reads
as successful while nothing has changed.

Check by hand as well:

```powershell
$h = Invoke-RestMethod https://web-production-192b7.up.railway.app/api/health
$h | Select-Object db_backend, db_backend_refused, schema_version, users, jobs
$h.db_pool
```

Expect `db_backend: postgres`, `db_backend_refused: null`, `schema_version: 11`,
**9 users, ~2,600 jobs**. A healthy box reporting 0 users is the failure this
whole procedure is about — if you see it, roll back immediately.

### Step 2.8 — Then log in yourself

Health can be right while the app is wrong. Sign in, open the swipe queue, open
Analytics, approve one job and undo it. Two minutes.

### Rollback (any point after 2.5)

Set `DB_BACKEND` back to `sqlite` — one variable, one redeploy, ~60 seconds. The
SQLite volume has been sitting there untouched the whole time, so you lose only
whatever was written to Postgres in the interval. That is why the quiet window in
2.0 matters: it makes that interval boring.

Do **not** delete `jobhunter_prod` on a rollback. Leave it; you will want to see
what it contains.

---

## Part 3 — Two things I found that are not on either list, and are yours

Neither is a cutover step. Both are one-line Railway changes that should happen
around the same time, and I cannot do either from here.

### 3.1 — Production and staging share one encryption key 🔴

`/api/health` reports `credentials_key: 7a46c58824d9` on **both**. That is the
fingerprint, not the key — but two environments reporting the same fingerprint
means the same `JH_ENCRYPTION_KEY` is set in both. Staging credentials can
decrypt production credentials.

Generate a **new, separate** key for staging and rotate staging to it:

```powershell
python -c "import secrets;print(secrets.token_urlsafe(48))"
```

Never a value pasted into a chat — including this one. Rotating staging (not
production) is the cheaper direction: staging's stored credentials are test
values, and `scripts/encrypt_credentials.py` can re-encrypt them.

### 3.2 — Email notifications: set `RESEND_FROM` before the first public signup 🔴

`send_email()` took a recipient and threw it away — `actual_to =
RESEND_VERIFIED_EMAIL` — so **every user's email notification went to your
inbox**, and both the caller and the notification log recorded "Sent OK".
Proven 2026-09-20 by driving `deliver_notification` for a user whose address is
`dana@somewhere-else.test`: Resend was handed `to=['eran.ganot@gmail.com']`.

It was a leftover from Resend's shared sandbox sender (`onboarding@resend.dev`),
which genuinely can only deliver to the account's own verified address.

**Fixed in code, in the build you are about to ship**: while the sender is still
the sandbox address, a send to anyone else is **refused** rather than redirected.
The callers already turn that into a logged `FAILED`, so the state of the world
becomes "nobody got it, and it says so" instead of "the wrong person got it, and
it says delivered". Admin alerts to your own address keep working unchanged.

**What is left for you, in Railway:**

1. Verify a domain in Resend (Resend dashboard → Domains → add and verify the
   domain you want mail to come from).
2. Railway → **production** → **web** → Variables:

   | Variable | Value |
   |---|---|
   | `RESEND_FROM` | `Job Hunter <alerts@yourdomain>` — an address on the domain you just verified |

3. Send yourself a test from Settings → Notifications → email → *Test*, then
   have one other user do the same. Before this, that test button reported
   "connection works!" to a user whose message had gone to you.

Until `RESEND_FROM` is set, email notifications fail for everyone except you —
by design. That is the correct behaviour for a sandbox sender, and it is
strictly better than the alternative it replaced.

## Order of operations, condensed

1. `ship.ps1` + `smoke.ps1` — get this build out.
2. Read `llm_history`, set `JH_LLM_GLOBAL_CALLS` / `JH_LLM_USER_CALLS`. (Part 1)
3. Verify a Resend domain and set `RESEND_FROM`. (3.2 — blocks public signup, not the cutover)
4. Rotate staging's encryption key. (3.1)
5. Backup → create db → migrate → **verify** → three variables together → smoke
   pinned to postgres. (Part 2)

Steps 2–4 are independent of 5 and of each other. Step 5 is the only one with a
rollback plan, because it is the only one that needs one.
