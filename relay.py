"""
relay.py — Mac-side relay that bridges local scheduled-task JSON files to Railway.

Run this on your Mac alongside your scheduled tasks:
    python3 relay.py

What it does (every 30 seconds):
  1. If pending_jobs.json exists   → POST /api/sync/jobs    → delete local file
  2. If applied_updates.json exists → POST /api/sync/updates → delete local file
  3. If notify.json exists          → POST /api/sync/notify  → delete local file
  4. Always: GET /api/sync/approved → overwrite approved_jobs.json locally
             so the apply-task can read it

All Railway calls are authenticated with SYNC_API_KEY.
RAILWAY_URL and SYNC_API_KEY are loaded from config.json (same file app.py uses).
"""

import json
import os
import time
import urllib.request
import urllib.error

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _post(url: str, payload: list | dict, api_key: str) -> dict:
    """POST JSON payload to url, authenticated with api_key. Returns response dict."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{url}?api_key={api_key}",
        data=body,
        method="POST"
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _get(url: str, api_key: str) -> list | dict:
    """GET url authenticated with api_key. Returns parsed JSON."""
    req = urllib.request.Request(
        f"{url}?api_key={api_key}",
        method="GET"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

# ── Sync tasks ────────────────────────────────────────────────────────────────

def sync_pending_jobs(base_dir: str, railway_url: str, api_key: str):
    path = os.path.join(base_dir, "pending_jobs.json")
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            jobs = json.load(f)
        if not jobs:
            os.remove(path)
            return
        result = _post(f"{railway_url}/api/sync/jobs", jobs, api_key)
        print(f"[relay] ✓ pending_jobs → Railway: {result}")
        os.remove(path)
    except Exception as e:
        print(f"[relay] ✗ pending_jobs error: {e}")


def sync_applied_updates(base_dir: str, railway_url: str, api_key: str):
    path = os.path.join(base_dir, "applied_updates.json")
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            updates = json.load(f)
        if not updates:
            os.remove(path)
            return
        result = _post(f"{railway_url}/api/sync/updates", updates, api_key)
        print(f"[relay] ✓ applied_updates → Railway: {result}")
        os.remove(path)
    except Exception as e:
        print(f"[relay] ✗ applied_updates error: {e}")


def sync_notify(base_dir: str, railway_url: str, api_key: str):
    path = os.path.join(base_dir, "notify.json")
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            payload = json.load(f)
        if not payload:
            os.remove(path)
            return
        result = _post(f"{railway_url}/api/sync/notify", payload, api_key)
        print(f"[relay] ✓ notify → Railway: {result}")
        os.remove(path)
    except Exception as e:
        print(f"[relay] ✗ notify error: {e}")


def fetch_approved_jobs(base_dir: str, railway_url: str, api_key: str):
    """Pull approved jobs from Railway and write them locally for the apply-task."""
    try:
        jobs = _get(f"{railway_url}/api/sync/approved", api_key)
        path = os.path.join(base_dir, "approved_jobs.json")
        with open(path, "w") as f:
            json.dump(jobs, f, indent=2, default=str)
        if jobs:
            print(f"[relay] ✓ fetched {len(jobs)} approved job(s) from Railway")
    except Exception as e:
        print(f"[relay] ✗ fetch approved_jobs error: {e}")

# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    print("[relay] Starting Job Hunter relay…")
    print(f"[relay] Base dir: {BASE_DIR}")

    cfg = load_config()
    railway_url = os.environ.get("RAILWAY_URL") or cfg.get("railway_url", "").rstrip("/")
    api_key     = os.environ.get("SYNC_API_KEY") or cfg.get("sync_api_key", "")

    if not railway_url:
        print("[relay] ✗ railway_url is not set in config.json — edit config.json and restart.")
        return
    if not api_key:
        print("[relay] ✗ sync_api_key is not set in config.json — edit config.json and restart.")
        return

    print(f"[relay] Railway URL : {railway_url}")
    print(f"[relay] Sync key    : {api_key[:8]}…")
    print("[relay] Polling every 30 seconds. Press Ctrl+C to stop.\n")

    while True:
        try:
            sync_pending_jobs(BASE_DIR, railway_url, api_key)
            sync_applied_updates(BASE_DIR, railway_url, api_key)
            sync_notify(BASE_DIR, railway_url, api_key)
            fetch_approved_jobs(BASE_DIR, railway_url, api_key)
        except Exception as e:
            print(f"[relay] Unexpected error: {e}")
        time.sleep(30)


if __name__ == "__main__":
    main()
