# Deploying Job Hunter

Production runs on Railway: one web service built from the [Dockerfile](Dockerfile), plus a Postgres database. Railway deploys `main` automatically on every merge.

## Release flow

Every change ships as a pull request:

1. **Branch from `main`** (`step-<n>-<slug>`), make the change with tests, and run the full suite: `python3 -m pytest -q`.
2. **Smoke-test locally before merging:** `python3 scripts/smoke.py --boot-local` boots the branch on a throwaway database and runs the suite plus every smoke check. Paste the summary into the PR.
3. **CI** runs the suite on SQLite and on Postgres. Merge only when both are green.
4. **Merge.** Railway builds and deploys `main`.
5. **Smoke-test production:**
   ```bash
   python3 scripts/smoke.py --skip-local --expect-commit <merge sha> --wait-for-deploy-minutes 8
   ```
   It waits until `/api/health` reports the merge commit, then runs the checks against production. Set `JH_HEALTH_KEY` in your shell to include the checks that need the detailed health view.

If the production smoke is red, revert the PR first and investigate second.

## Web app changes

Railway does not build the frontend. A change under `web/src` needs `python3 scripts/build_web.py` and a commit of the resulting `web_bundle/` in the same PR.

## Database changes

Schema changes are numbered migrations in [migrations.py](migrations.py). They run at startup, in order, once each; the applied versions are recorded in `schema_migrations`. A migration must be idempotent and must work on both SQLite and Postgres (CI checks both).

## Configuration

Set environment variables in the Railway service's Variables; [.env.example](.env.example) lists them all. Changing a variable redeploys the service.

## Operations

[RAILWAY_RUNBOOK.md](RAILWAY_RUNBOOK.md) covers backups, the Postgres cutover, restores and incident steps. `/api/health` is the first place to look: running commit, schema version, database, worker, and paid-source usage.
