#!/usr/bin/env python3
"""End-to-end smoke test for the director-cut Fly deployment.

How to get your Supabase access token (GitHub OAuth users):
─────────────────────────────────────────────────────────────
1. Open https://wmstudio.io in your browser (sign in if needed)
2. Press F12 → click the "Console" tab
3. Paste exactly this line and press Enter:
       JSON.parse(localStorage.getItem('sb-gnlktspxhdthoveoyucs-auth-token')).access_token
4. Copy the long string that appears (starts with "eyJ...")
5. Run this script with that token:

       SUPABASE_TOKEN="eyJ..." python3 scripts/smoke_test.py

──────────────────────────────────────────────────────────────
Alternatively, if you have email/password credentials:
       python3 scripts/smoke_test.py
(It will prompt for email + password.)
"""

import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = "https://director-cut.fly.dev"
SUPABASE_URL = "https://gnlktspxhdthoveoyucs.supabase.co"
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdubGt0c3B4aGR0aG92ZW95dWNzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTM1MTQyMDIsImV4cCI6MjA2OTA5MDIwMn0"
    ".-1pZ9_UsQwIWnR5Gf84OMwr-zkfwgVozh5RN-mtQ-6Q"
)

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _req(method: str, url: str, body=None, token: str | None = None,
         extra_headers: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        text = e.read().decode(errors="replace")
        print(f"  ✗ HTTP {e.code} from {url}: {text[:400]}", file=sys.stderr)
        raise


def get(url: str, token: str) -> dict:
    return _req("GET", url, token=token)


def post(url: str, body: dict, token: str | None = None, extra_headers: dict | None = None) -> dict:
    return _req("POST", url, body=body, token=token, extra_headers=extra_headers)


# ── Step 1 — get token ────────────────────────────────────────────────────────

def get_token() -> str:
    # Prefer env var (paste from browser localStorage)
    tok = os.environ.get("SUPABASE_TOKEN", "").strip()
    if tok:
        print(f"\n[1/5] Using SUPABASE_TOKEN from environment ✓")
        return tok

    print("\n[1/5] No SUPABASE_TOKEN env var found.")
    print()
    print("  GitHub OAuth users — get your token from the browser:")
    print("  1. Open https://wmstudio.io and sign in")
    print("  2. Press F12 → Console tab")
    print("  3. Paste this and press Enter:")
    print("       JSON.parse(localStorage.getItem('sb-gnlktspxhdthoveoyucs-auth-token')).access_token")
    print("  4. Copy the long 'eyJ...' string")
    print("  5. Re-run:  SUPABASE_TOKEN=\"eyJ...\" python3 scripts/smoke_test.py")
    print()
    print("  ── OR ── sign in with email/password below:")
    print()

    email = input("  Email [jessi316866@gmail.com]: ").strip() or "jessi316866@gmail.com"
    password = getpass.getpass("  Password: ")
    if not password:
        print("Password required. See instructions above for GitHub OAuth.", file=sys.stderr)
        sys.exit(1)

    print(f"\n  Signing in as {email} …")
    resp = post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        {"email": email, "password": password},
        extra_headers={"apikey": ANON_KEY},
    )
    tok = resp.get("access_token", "")
    if not tok:
        print("  ✗ No access_token returned. Check credentials.", file=sys.stderr)
        sys.exit(1)
    print("  ✓ Signed in")
    return tok


# ── Step 2 — verify token against the Fly app ────────────────────────────────

def verify_token(token: str) -> None:
    print("\n[2/5] Verifying token against Fly app …")
    try:
        get(f"{BASE}/api/runs/", token=token)
        print("  ✓ Token accepted")
    except Exception:
        print("  ✗ Token rejected by the backend — it may have expired.")
        print("    Get a fresh one from the browser (see instructions at top of script).")
        sys.exit(1)


# ── Step 3 — create project + run ────────────────────────────────────────────

def create_project(token: str) -> str:
    ts = int(time.time())
    resp = post(f"{BASE}/api/projects/",
                {"name": f"smoke-test-{ts}", "description": "automated e2e test"},
                token=token)
    pid = resp.get("id", "")
    if not pid:
        print(f"  ✗ No project id: {resp}", file=sys.stderr); sys.exit(1)
    return pid


def create_run(token: str, project_id: str) -> tuple[str, str]:
    print("\n[3/5] Creating project + run …")
    pid = create_project(token)
    resp = post(f"{BASE}/api/runs/", {
        "project_id": pid,
        "prompt": "A single red circle on a white background, still image",
        "settings": {"target_output": "image", "scene_count": 1, "max_scenes": 1},
    }, token=token)
    run_id = resp.get("id", "")
    status  = resp.get("status", "?")
    last_error = resp.get("last_error")
    if not run_id:
        print(f"  ✗ No run id: {resp}", file=sys.stderr); sys.exit(1)
    if status == "failed":
        print(f"  ✗ Run immediately failed! last_error: {last_error}")
        sys.exit(1)
    print(f"  ✓ Project: {pid}")
    print(f"  ✓ Run:     {run_id}  (status={status})")
    return run_id, pid


# ── Step 4 — poll ─────────────────────────────────────────────────────────────

def poll(token: str, run_id: str, max_minutes: int = 20) -> dict:
    print(f"\n[4/5] Polling every 10 s (up to {max_minutes} min — render can take ~5-10 min) …")
    deadline = time.time() + max_minutes * 60
    tick = 0
    last_stage = ""
    while time.time() < deadline:
        tick += 1
        time.sleep(10)
        try:
            state = get(f"{BASE}/api/runs/{run_id}", token=token)
        except Exception:
            print(f"  [{tick:03d}] Poll failed — retrying …")
            continue

        status     = state.get("status", "?")
        stage      = state.get("current_stage", "?")
        last_error = state.get("last_error") or ""

        if stage != last_stage:
            print(f"  [{tick:03d}] ▶ {stage}", flush=True)
            last_stage = stage
        else:
            print(f"  [{tick:03d}]   {status}/{stage}" + (f"  ← {last_error[:80]}" if last_error else ""), flush=True)

        if status in ("completed", "failed", "cancelled"):
            return state

    print(f"  ✗ Timed out after {max_minutes} min")
    return get(f"{BASE}/api/runs/{run_id}", token=token)


# ── Step 5 — report ───────────────────────────────────────────────────────────

def report(token: str, run_id: str, final_state: dict):
    status = final_state.get("status")
    print(f"\n[5/5] Final status: {status.upper()}")

    if status == "completed":
        print("  ✅ Pipeline completed!")
        try:
            out = get(f"{BASE}/api/runs/{run_id}/outputs", token=token)
            print(f"    video_url   : {out.get('video_url') or '(none)'}")
            print(f"    image_urls  : {out.get('image_urls') or '(none)'}")
            print(f"    preview_urls: {out.get('preview_urls') or '(none)'}")
        except Exception as e:
            print(f"    (could not fetch outputs: {e})")
    else:
        last_error = final_state.get("last_error") or "(none)"
        print(f"  ❌ Run did not complete. last_error: {last_error}")
        try:
            errs = get(f"{BASE}/api/runs/{run_id}/errors", token=token)
            print("  Full error list:")
            for e in errs.get("errors", []):
                print(f"    [{e.get('stage','?')}] {e.get('message')}")
        except Exception as ex:
            print(f"  (could not fetch errors: {ex})")

    print(f"\n  Run URL: {BASE}/api/runs/{run_id}")
    print(f"  Errors : {BASE}/api/runs/{run_id}/errors")
    print(f"  Outputs: {BASE}/api/runs/{run_id}/outputs")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Director-Cut smoke test — https://director-cut.fly.dev")
    print("=" * 60)

    token   = get_token()
    verify_token(token)
    run_id, _ = create_run(token, "")
    final_state = poll(token, run_id, max_minutes=20)
    report(token, run_id, final_state)


if __name__ == "__main__":
    main()
