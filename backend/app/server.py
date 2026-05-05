"""FastAPI server – the HTTP surface that Tauri talks to."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from pathlib import Path
from urllib.parse import urlencode

# ── Inline env layering (reads wmstudio .env when WMSTUDIO_PROJECT_ROOT is set)
ENV_FILES_TRIED: list[str] = []


def _record_try(p: Path) -> None:
    try:
        ENV_FILES_TRIED.append(str(p.expanduser().resolve()))
    except OSError:
        ENV_FILES_TRIED.append(str(p.expanduser()))


def _apply_env_lines(text: str) -> None:
    """Merge keys so later-loaded files override earlier, but don't overwrite existing env."""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        k, v = key.strip(), val.strip()
        if k and k not in os.environ:
            os.environ[k] = v

def load_layered_env_once() -> None:
    ENV_FILES_TRIED.clear()
    backend_dir = Path(__file__).resolve().parent.parent
    director_dir = backend_dir.parent
    mono_root = director_dir.parent

    explicit = (os.environ.get("DIRECTOR_ENV_FILE") or "").strip()
    if explicit:
        p = Path(explicit).expanduser()
        _record_try(p)
        if p.is_file():
            _apply_env_lines(p.read_text())

    wm_root = (os.environ.get("WMSTUDIO_PROJECT_ROOT") or "").strip()
    if wm_root:
        rp = Path(wm_root).expanduser().resolve()
        for rel in (".env", "directorr/backend/.env", "directorr/.env"):
            p = rp / rel
            _record_try(p)
            if p.is_file():
                _apply_env_lines(p.read_text())

    for env_path in (mono_root / ".env", director_dir / ".env", backend_dir / ".env"):
        _record_try(env_path)
        if env_path.is_file():
            _apply_env_lines(env_path.read_text())

    # One-line bootstrap trace — check Python stdout (Tauri/backend terminal).
    app_url = (os.getenv("NEXT_PUBLIC_APP_URL") or "").strip()
    bk_url = (os.getenv("NEXT_PUBLIC_BACKEND_URL") or "").strip()
    supa = bool((os.getenv("NEXT_PUBLIC_SUPABASE_URL") or "").strip())
    print(
        f"[DIRECTOR_BOOT] cwd={os.getcwd()!s} backend_dir={backend_dir} "
        f"mono_root_dotenv={(mono_root / '.env')} exists={(mono_root / '.env').is_file()} "
        f"APP_URL={app_url or '<<<MISSING>>>'} BACKEND_URL={bk_url or '(none)'} supabase_public_ok={supa} "
        f"env_sources={len(ENV_FILES_TRIED)}",
        flush=True,
    )


load_layered_env_once()
import sys
print(f"[DIRECTOR_PYTHON] SUPABASE_URL from env: {os.getenv('NEXT_PUBLIC_SUPABASE_URL')}", file=sys.stderr, flush=True)

from fastapi import FastAPI, Request, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import httpx  # noqa: E402
from html import escape  # noqa: E402


app = FastAPI(title="Director's Cut", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Serve rendered exports as static files (video playback — localhost-only; no Bearer on <video>)

_export_dir = Path(__file__).resolve().parent.parent / "data" / "exports"
_export_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media/exports", StaticFiles(directory=str(_export_dir)), name="exports")


async def validate_supabase_session(access_token: str) -> dict:
    """Same trust model as wmstudio: verify JWT against Supabase Auth (GoTrue user endpoint)."""
    base = (os.getenv("NEXT_PUBLIC_SUPABASE_URL") or "").rstrip("/")
    anon = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY") or ""
    if not base or not anon or not access_token.strip():
        raise HTTPException(status_code=503, detail="Supabase auth is not configured on the Director backend")
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{base}/auth/v1/user",
            headers={"Authorization": f"Bearer {access_token.strip()}", "apikey": anon},
        )
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired WM Studio session")
    return r.json()


def extract_bearer_access_token(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        tok = auth[7:].strip()
        return tok if tok else None
    if request.url.path.startswith("/api/events/stream/"):
        q = request.query_params.get("access_token")
        if q and q.strip():
            return q.strip()
    return None


PUBLIC_PATH_EXACT = frozenset(
    {
        "/health",
        "/auth/callback",
        "/auth/import-session",
        "/api/auth/config",
        "/api/auth/desktop-oauth-bridge",
        "/api/auth/oauth/start",
        "/api/auth/oauth/complete",
    }
)

# One-shot handoff when OAuth finishes in the system browser (no window.opener in Tauri/WKWebView).
_desktop_oauth_bridge: dict[str, str | int | None] | None = None

# Last PKCE verifier for the most recent /oauth/start (desktop: one flow at a time).
_oauth_pkce_pending_verifier: str | None = None

_DIRECTOR_BACKEND_PORT = (os.getenv("DIRECTOR_PORT") or "9420").strip() or "9420"
_DIRECTOR_PUBLIC_CALLBACK = f"http://127.0.0.1:{_DIRECTOR_BACKEND_PORT}/auth/callback"


def _pkce_challenge(verifier: str) -> str:
    d = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(d).rstrip(b"=").decode()


async def _exchange_supabase_pkce(auth_code: str, code_verifier: str) -> dict:
    """GoTrue picks grant_type via FormValue (use query); PKCE params are JSON-only (retrieveRequestParams)."""
    base = (
        (os.getenv("NEXT_PUBLIC_SUPABASE_URL") or "")
        .strip()
        .replace("</", "")
        .replace("<", "")
        .rstrip("/")
    )
    anon = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY") or ""
    if not base or not anon:
        raise RuntimeError("Supabase is not configured on the Director backend")

    headers = {
        "apikey": anon,
        "Authorization": f"Bearer {anon}",
        "Content-Type": "application/json",
    }
    # grant_type must be query/form (FormValue); auth_code + code_verifier must be JSON body.
    url = f"{base}/auth/v1/token?grant_type=pkce"
    payload_auth = {"auth_code": auth_code.strip(), "code_verifier": code_verifier}
    payload_code = {"code": auth_code.strip(), "code_verifier": code_verifier}

    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=headers, json=payload_auth)
        if r.status_code != 200:
            r2 = await client.post(url, headers=headers, json=payload_code)
            if r2.status_code == 200:
                return r2.json()
            msg = (r2.text or r.text)[:900] if (r2.text or r.text) else f"HTTP {r2.status_code}"
            raise RuntimeError(msg)
    return r.json()


def _set_desktop_bridge_from_tokens(data: dict) -> None:
    global _desktop_oauth_bridge
    at = (data.get("access_token") or data.get("token") or "").strip()
    rt = (data.get("refresh_token") or "").strip()
    if not at or not rt:
        raise RuntimeError("Token response missing access or refresh token")
    _desktop_oauth_bridge = {
        "access_token": at,
        "refresh_token": rt,
        "expires_in": data.get("expires_in"),
    }


@app.middleware("http")
async def require_wmstudio_auth(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path
    if path in PUBLIC_PATH_EXACT or path == "/favicon.ico":
        return await call_next(request)
    if path.startswith("/media"):
        # allow /media/exports/… for <video> tags (no Authorization header possible)
        return await call_next(request)

    tok = extract_bearer_access_token(request)
    if not tok:
        from starlette.responses import JSONResponse

        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        request.state.director_user = await validate_supabase_session(tok)
    except HTTPException as e:
        from starlette.responses import JSONResponse

        body = getattr(e, "detail", None) or "Unauthorized"
        if isinstance(body, dict):
            return JSONResponse(body, status_code=e.status_code)
        return JSONResponse({"detail": str(body)}, status_code=e.status_code)
    return await call_next(request)


@app.get("/api/auth/config")
async def api_auth_config():
    """Expose public Supabase + wmstudio origins (same NEXT_PUBLIC_* as the web app)."""
    url = (os.getenv("NEXT_PUBLIC_SUPABASE_URL") or "").strip()
    anon = (os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY") or "").strip()
    wm_origin = (
        os.getenv("NEXT_PUBLIC_APP_URL")
        or os.getenv("NEXT_PUBLIC_BACKEND_URL")
        or ""
    ).strip().rstrip("/")
    locale = (os.getenv("WMSTUDIO_DEFAULT_LOCALE") or "en").strip() or "en"
    oauth_redirect = _DIRECTOR_PUBLIC_CALLBACK
    groq_missing = not (os.getenv("GROQ_API_KEY") or "").strip()
    fal_missing = not (os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or "").strip()
    payload = {
        "supabase_url": url,
        "supabase_anon_key": anon,
        "wmstudio_origin": wm_origin,
        "locale": locale,
        "oauth_redirect": oauth_redirect,
        "groq_missing": groq_missing,
        "fal_missing": fal_missing,
        "env_files_tried": ENV_FILES_TRIED,
    }
    if not url or not anon:
        payload["hint"] = (
            "Add NEXT_PUBLIC_SUPABASE_URL + NEXT_PUBLIC_SUPABASE_ANON_KEY to wmstudio or director backend .env, "
            "or set WMSTUDIO_PROJECT_ROOT to your wmstudio folder."
        )
    print(
        f"[DIRECTOR_BOOT] /api/auth/config wmstudio_origin={wm_origin!r} "
        f"APP_URL_raw={repr(os.getenv('NEXT_PUBLIC_APP_URL'))} BACKEND_URL_raw={repr(os.getenv('NEXT_PUBLIC_BACKEND_URL'))}",
        flush=True,
    )
    return payload


@app.post("/api/auth/desktop-oauth-bridge")
async def desktop_oauth_bridge_post(request: Request) -> dict:
    """Store tokens from /auth/callback when opened in system browser (Tauri blocks window.opener)."""
    global _desktop_oauth_bridge
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Expected JSON body")
    at = (data.get("access_token") or "").strip()
    rt = (data.get("refresh_token") or "").strip()
    if not at or not rt:
        raise HTTPException(status_code=400, detail="access_token and refresh_token required")
    _desktop_oauth_bridge = {
        "access_token": at,
        "refresh_token": rt,
        "expires_in": data.get("expires_in"),
    }
    return {"ok": True}


@app.get("/api/auth/desktop-oauth-bridge")
async def desktop_oauth_bridge_get() -> dict:
    """Poll once: returns tokens and clears the slot (single-user desktop)."""
    global _desktop_oauth_bridge
    if not _desktop_oauth_bridge:
        return {}
    out = dict(_desktop_oauth_bridge)
    _desktop_oauth_bridge = None
    return out


_OAUTH_PROVIDER_SET = frozenset({"google", "github", "apple"})


@app.get("/api/auth/oauth/start")
async def api_oauth_start(provider: str) -> dict:
    """Build Supabase /authorize URL with server-held PKCE (required when OAuth completes in system browser)."""
    p = (provider or "").strip().lower()
    if p not in _OAUTH_PROVIDER_SET:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider!r}")

    base = (
        (os.getenv("NEXT_PUBLIC_SUPABASE_URL") or "")
        .strip()
        .replace("</", "")
        .replace("<", "")
        .rstrip("/")
    )
    anon = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY") or ""
    if not base or not anon:
        raise HTTPException(status_code=503, detail="Supabase keys missing")

    global _oauth_pkce_pending_verifier
    verifier = secrets.token_urlsafe(48)
    _oauth_pkce_pending_verifier = verifier
    challenge = _pkce_challenge(verifier)

    # Do NOT pass query param "state". GoTrue reserves it for internal OAuth/session
    # tracking; supplying our own yields bad_oauth_state and redirects may never reach
    # redirect_to on localhost → desktop bridge stays empty.

    qs = urlencode(
        {
            "provider": p,
            "redirect_to": _DIRECTOR_PUBLIC_CALLBACK,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    url = f"{base}/auth/v1/authorize?{qs}"
    return {"url": url}


@app.post("/api/auth/oauth/complete")
async def api_oauth_complete(request: Request) -> dict:
    """Complete PKCE in the backend: browser sends ?code… (query or fragment never hits GET body)."""
    global _oauth_pkce_pending_verifier
    code = ""
    try:
        body = await request.json()
        if isinstance(body, dict):
            code = str(body.get("code") or "").strip()
    except Exception:
        pass
    if not code:
        raise HTTPException(status_code=400, detail="JSON body must include {\"code\":\"…\"}")

    verifier = _oauth_pkce_pending_verifier
    if not verifier:
        raise HTTPException(
            status_code=400,
            detail=(
                "No pending OAuth verifier. Close this tab and start sign-in again from Director's Cut."
            ),
        )
    _oauth_pkce_pending_verifier = None
    print(
        f"[DIRECTOR_BOOT] POST /api/auth/oauth/complete code_len={len(code)} verifier_consumed=yes",
        flush=True,
    )
    try:
        tokens = await _exchange_supabase_pkce(code, verifier)
        _set_desktop_bridge_from_tokens(tokens)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)[:500]) from e
    print("[DIRECTOR_BOOT] PKCE exchange OK → bridge ready", flush=True)
    return {"ok": True}


@app.get("/auth/import-session")
async def auth_import_wmstudio_session_page() -> HTMLResponse:
    """Receives wm.studio handoff (# JSON with tokens) from top-level HTTPS→HTTP navigation."""
    return HTMLResponse(
        """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/><title>Import session</title></head>
<body style="margin:40px;font-family:system-ui">
<p id="m">Importing WM Studio session…</p>
<script type="module">
const m = document.getElementById("m");
const raw = decodeURIComponent((location.hash || "").replace(/^#/, ""));
if (!raw.trim()) {
  if (m) m.textContent = "Missing session data.";
} else {
  try {
    const j = JSON.parse(raw);
    const r = await fetch("/api/auth/desktop-oauth-bridge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        access_token: j.access_token,
        refresh_token: j.refresh_token,
        expires_in: j.expires_in,
      }),
    });
    if (r.ok) {
      if (m) m.textContent = "Session linked. Return to Director's Cut.";
    } else if (m) {
      m.textContent = "Failed: " + (await r.text()).slice(0, 220);
    }
  } catch (e) {
    if (m) m.textContent = String(e);
  }
}
</script></body></html>"""
    )


@app.get("/auth/callback")
async def supabase_oauth_callback(request: Request) -> HTMLResponse:
    """OAuth redirect lands here with ?code=… or …#access_token…. Server never receives hash; HTML POSTs code for PKCE."""
    oauth_err = (request.query_params.get("error_description") or "").strip() or (
        request.query_params.get("error") or ""
    ).strip()
    if oauth_err:
        return HTMLResponse(
            f"<html><body style='margin:40px;font-family:system-ui'><p><strong>Sign-in error</strong></p>"
            f"<p>{escape(oauth_err)}</p>"
            "</body></html>",
            status_code=400,
        )

    # Optional: log when GoTrue sent code on query (fast path used to work only for query).
    qcode = (request.query_params.get("code") or "").strip()
    if qcode:
        print(
            f"[DIRECTOR_BOOT] GET /auth/callback query code_len={len(qcode)} (finish via POST /api/auth/oauth/complete)",
            flush=True,
        )

    shim = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/><title>Director sign-in</title></head>
<body style="margin:40px;font-family:system-ui;line-height:1.45">
<p id="m">Finishing sign-in…</p>
<script>
(function() {
  var m = document.getElementById("m");
  function params(s) { return new URLSearchParams((s || "").replace(/^\\?/, "")); }
  var search = window.location.search || "";
  var hash = (window.location.hash && window.location.hash.length > 1) ? window.location.hash.slice(1) : "";
  var err = params(search).get("error_description") || params(search).get("error")
    || params(hash).get("error_description") || params(hash).get("error");
  if (err) { m.textContent = "Sign-in error: " + err; return; }
  var code = params(search).get("code") || params(hash).get("code");
  if (!code) {
    m.textContent = "No authorization code in this URL. In Supabase Dashboard → Authentication → URL configuration, add this exact redirect URL: http://127.0.0.1:9420/auth/callback";
    return;
  }
  fetch("/api/auth/oauth/complete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code: code })
  }).then(function(r) {
    return r.text().then(function(txt) {
      var j = null;
      try { j = JSON.parse(txt); } catch (e) {}
      if (!r.ok) {
        var d = j && j.detail;
        throw new Error(typeof d === "string" ? d : (txt || r.status).slice(0, 400));
      }
      m.textContent = "Signed in — switch back to Director's Cut.";
    });
  }).catch(function(e) {
    m.textContent = String(e && e.message ? e.message : e);
  });
})();
</script></body></html>"""
    return HTMLResponse(shim)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.on_event("startup")
async def startup():
    """Initialize DB on startup and restore saved settings into env."""
    from app.db.connection import get_db

    db = await get_db()
    await db.close()
    try:
        from app.db.repository import SettingsRepository

        repo = SettingsRepository()
        saved = await repo.get_all()
        _env_map = {
            "groq_api_key": "GROQ_API_KEY",
            "fal_api_key": "FAL_KEY",
            "video_model": "FAL_VIDEO_MODEL",
        }
        for skey, evar in _env_map.items():
            val = saved.get(skey)
            if val:
                os.environ[evar] = str(val)
        if saved.get("fal_api_key"):
            os.environ["FAL_API_KEY"] = str(saved["fal_api_key"])
    except Exception as e:
        print(f"⚠️ Could not restore settings: {e}")

    url_ok = bool((os.getenv("NEXT_PUBLIC_SUPABASE_URL") or "").strip())
    anon_ok = bool((os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY") or "").strip())
    if not (url_ok and anon_ok):
        print("⚠️ Supabase public vars missing after .env load. Tried:")
        for p in ENV_FILES_TRIED:
            print(f"   - {p}")
        print("   Point WMSTUDIO_PROJECT_ROOT at your wmstudio repo, set DIRECTOR_ENV_FILE, or add keys to backend .env.")

    print("✅ Director's Cut backend ready on http://127.0.0.1:9420")


# ── Protected API routers

from app.routes import projects, runs, artifacts, approvals, settings, events  # noqa: E402

app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(runs.router, prefix="/api/runs", tags=["runs"])
app.include_router(artifacts.router, prefix="/api/artifacts", tags=["artifacts"])
app.include_router(approvals.router, prefix="/api/approvals", tags=["approvals"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(events.router, prefix="/api/events", tags=["events"])