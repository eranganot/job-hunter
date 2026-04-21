# Deployment Guide

## Branch Strategy

| Branch | Railway Environment | Purpose |
|--------|-------------------|---------|
| `dev`  | **Staging**       | All development work goes here first |
| `main` | **Production**    | Only receives merges from `dev` after staging validation |

## Daily Workflow

### 1. All changes go to `dev` first

Push every commit to the `dev` branch. Claude will do this automatically.

### 2. Validate on Staging

- Open the Staging URL on Railway
- Test the specific feature changed
- Check Railway Deploy Logs for errors

### 3. Promote to Production

Only after staging validates:
```bash
git checkout main
git merge dev
git push origin main
```

Railway Production auto-deploys. Done.

---

## Railway Setup (one-time, manual step)

### Production environment
- Service source branch: `main` (already set)

### Staging environment
- Go to Railway -> Staging environment -> your service -> Settings -> Source
- Change branch from `main` to `dev`
- Save

---

## Emergency Hotfix

If prod is broken and needs an immediate fix without unfinished dev work:

1. Fix directly on `main`
2. Push to `main` -> prod deploys
3. Cherry-pick or merge the fix back to `dev` to keep branches in sync

---

## Environment Resources

| Resource | Staging | Production |
|----------|---------|------------|
| Database | Separate SQLite volume | Separate SQLite volume |
| Auto-deploy branch | `dev` | `main` |
| API keys | Same env vars | Same env vars |

---

## Rules

1. Never push directly to `main` -- always dev -> staging validation -> merge to main
2. Every commit to `dev` triggers a staging deploy automatically
3. Always syntax-check Python before pushing: `python3 -m py_compile app.py ai_analysis.py`
4. Test the changed feature on staging before merging to main
