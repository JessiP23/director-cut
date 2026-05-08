# Headless FastAPI backend (no Tauri window)

The desktop shell is optional: the HTTP API is the same **FastAPI** app Tauri spawns. You can run it alone for automation, MCP bridges (for example director-mcp on Fly with `Authorization: Bearer <Supabase JWT>`), or container deployment.

## Is your approach achievable?

Yes. Replacing a **tunnel to localhost** with a stable HTTPS origin (Fly, etc.) is the normal next step—the process is identical to what the app supervises (`uvicorn app.server:app`), only bind address and persistence differ.

**Desktop app UX** is unaffected if users still run Director’s Cut normally: the UI talks to **`http://127.0.0.1:<port>`** via the Rust bridge. That path does not need to become slower or “heavier” because of a hosted API.

Latency and capacity matter only for **clients that call the hosted URL** (for example MCP over the public internet). That is additive load, not overhead inside the WKWebView.

---

## How to run and test locally (step by step)

### 1. Prerequisites

- **Python 3.10+** on your machine (`python3 --version`).
- **`ffmpeg`** on your PATH if you hit pipeline/media routes (same as desktop): e.g. `brew install ffmpeg` on macOS.
- **Environment variables** the backend expects (same sources as desktop: `backend/.env`, repo root `.env`, or `DIRECTOR_ENV_FILE` pointing at a file). At minimum for auth checks:
  - `NEXT_PUBLIC_SUPABASE_URL`
  - `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- Optional for real runs / LLM: `GROQ_API_KEY`, `FAL_KEY` (or configure later via app Settings into SQLite).

### 2. Install and start the server

From the **repository root**, using a venv inside `backend/`:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install .
export DIRECTOR_PORT="${DIRECTOR_PORT:-9420}"
uvicorn app.server:app --host 127.0.0.1 --port "$DIRECTOR_PORT"
```

Leave this terminal open while you test.

### 3. Smoke tests (no login)

New terminal:

```bash
curl -sS "http://127.0.0.1:${DIRECTOR_PORT:-9420}/health"
curl -sS "http://127.0.0.1:${DIRECTOR_PORT:-9420}/mcp/health"
curl -sS "http://127.0.0.1:${DIRECTOR_PORT:-9420}/api/auth/config"
```

You want `/health` and `/mcp/health` to return `"ok"`-style JSON. `/api/auth/config` should echo your Supabase URL/key presence (helps confirm `.env` was picked up).

### 4. Test a protected `/api/` route

Most `/api/*` routes require:

```http
Authorization: Bearer <Supabase access token from your OAuth sign-in>
```

Use a real JWT from logging into your product (same token director-mcp would send). Example:

```bash
export TOKEN='paste-supabase-access-jwt-here'
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:${DIRECTOR_PORT:-9420}/api/projects"
```

401 means missing/invalid token; 503 on auth often means Supabase env vars missing on the backend.

### 5. Test MCP locally

Exact JSON-RPC payloads depend on your MCP client. Minimal checks:

- `GET http://127.0.0.1:9420/mcp/health` (no Bearer).
- Your Streamable HTTP client should target **`http://127.0.0.1:9420/mcp`** with **Bearer** set (see [MCP_INTEGRATION.md](../MCP_INTEGRATION.md)).

### 6. Avoid port clashes with the desktop app

Do **not** run headless uvicorn on **9420** at the same time as `cargo tauri dev` unless you stopped the embedded backend—or pick another port:

```bash
export DIRECTOR_PORT=19420
uvicorn app.server:app --host 127.0.0.1 --port 19420
```

---

## How to deploy (Docker → Fly.io)

### A. Sanity-check with Docker on your laptop

From **repo root**:

```bash
docker build -f backend/Dockerfile -t director-cut-api .

docker run --rm \
  -e PORT=8080 \
  -e NEXT_PUBLIC_SUPABASE_URL="https://YOUR_PROJECT.supabase.co" \
  -e NEXT_PUBLIC_SUPABASE_ANON_KEY="YOUR_ANON_KEY" \
  -e GROQ_API_KEY="your-key" \
  -e FAL_KEY="your-key" \
  -p 8080:8080 \
  director-cut-api
```

Then:

```bash
curl -sS http://127.0.0.1:8080/health
```

Optional: persist SQLite + exports in the container (same idea as Fly volumes):

```bash
docker run --rm \
  -e PORT=8080 \
  -e DIRECTOR_DB=/data/director_cut.sqlite \
  -v director-cut-data:/data \
  -e NEXT_PUBLIC_SUPABASE_URL="https://YOUR_PROJECT.supabase.co" \
  -e NEXT_PUBLIC_SUPABASE_ANON_KEY="YOUR_ANON_KEY" \
  -e GROQ_API_KEY="your-key" \
  -e FAL_KEY="your-key" \
  -p 8080:8080 \
  director-cut-api
```

### B. Fly.io: first-time deploy

1. Install **`flyctl`** and log in: [Fly.io install](https://fly.io/docs/hands-on/install-flyctl/).
2. From **repo root** (adjust `app` name):

```bash
fly launch --no-deploy \
  --name your-director-api \
  --region ord \
  --dockerfile backend/Dockerfile \
  --dockerignore backend/.dockerignore
```

If prompted, confirm it builds context from `./backend` or set **build** in `fly.toml` (below).

3. Set secrets (**not** in the Dockerfile):

```bash
fly secrets set \
  NEXT_PUBLIC_SUPABASE_URL="https://YOUR_PROJECT.supabase.co" \
  NEXT_PUBLIC_SUPABASE_ANON_KEY="YOUR_ANON_KEY" \
  GROQ_API_KEY="…" \
  FAL_KEY="…" \
  -a your-director-api
```

4. **Persistence (recommended):** attach a volume for SQLite + exports so data survives restarts (see Fly [volumes docs](https://fly.io/docs/reference/volumes/)). Point `DIRECTOR_DB` at e.g. `/data/director_cut.sqlite` and mount `/data`.

5. Deploy:

```bash
fly deploy -a your-director-api
```

6. Verify:

```bash
curl -sS "https://your-director-api.fly.dev/health"
```

7. Configure **director-mcp** (or mcp-director) **`DIRECTOR_BASE_URL`** to `https://your-director-api.fly.dev` (no trailing slash unless your MCP code expects one). MCP traffic goes to **`https://your-director-api.fly.dev/mcp`** when the client uses path `/mcp`.

### Example `fly.toml` fragment

Tune `internal_port` to match what the container listens on. The provided Dockerfile respects **`PORT`**; Fly Machines often set **`PORT=8080`**.

```toml
app = "your-director-api"
primary_region = "ord"

[build]
  dockerfile = "backend/Dockerfile"
  dockerignore = "backend/.dockerignore"

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = "stop"
  auto_start_machines = true

  [[http_service.checks]]
    interval = "15s"
    timeout = "2s"
    grace_period = "5s"
    method = "GET"
    path = "/health"
```

If Fly does **not** set `PORT`, add `[env]` with `PORT` matching **`http_service.internal_port`** (often `8080`). If the app listens on `9420` but Fly proxies to `8080`, health checks fail with “refused connection”.

```toml
[env]
  PORT = "8080"

[http_service]
  internal_port = 8080
```

---

## Inventory: canonical HTTP surface

| Item | Detail |
|------|--------|
| **ASGI app** | `app.server:app` (package `app`, module `server`) |
| **Alternate entry** | `main.py` re-exports the same app; `python main.py` runs uvicorn with **reload** (dev-oriented) |
| **Tauri prod command** | `python -m uvicorn app.server:app --host 127.0.0.1 --port <DIRECTOR_PORT>` with `cwd` = `backend/` (see `src-tauri/src/lib.rs`) |
| **`/health`** | `GET /health` → `{"status":"ok","version":"0.1.0"}` — **no auth** (`PUBLIC_PATH_EXACT`) |
| **`/mcp/health`** | `GET /mcp/health` → `{"status":"ok"}` — MCP-specific health (also unauthenticated via `/mcp` bypass in middleware) |
| **`/api/*`** | REST API under `/api/...` — requires `Authorization: Bearer <Supabase access_token>` (validated via `GET {SUPABASE_URL}/auth/v1/user` with anon `apikey`) except for listed public auth routes |
| **`/mcp`** | Streamable HTTP MCP — **own** Bearer check (Supabase session **or** desktop HS256 token from settings) |
| **`/media/exports`** | Static files for rendered video — **no Bearer** (browser `<video>` cannot send headers); treat as sensitive in threat models |
| **Default port** | `9420` — override with `DIRECTOR_PORT` |
| **Env loading** | `load_layered_env_once()` in `app/server.py`: optional `DIRECTOR_ENV_FILE`, `WMSTUDIO_PROJECT_ROOT`, then repo `.env` files (mono / director / `backend/.env`) — **does not overwrite** keys already set in the process environment |

**JWT / Supabase assumptions**

- “User JWT” means a **Supabase Auth access token** (GoTrue), not a service-role key.
- Validation: `validate_supabase_session()` calls Supabase with that Bearer token plus `NEXT_PUBLIC_SUPABASE_ANON_KEY` as `apikey`.
- MCP also accepts **desktop MCP tokens** signed with `desktop_mcp_hs256_secret` from SQLite (or `DIRECTOR_DESKTOP_MCP_SECRET`).

---

## Optional: auto-reload while editing Python

```bash
cd backend && source .venv/bin/activate
uvicorn main:app --reload --host 127.0.0.1 --port "${DIRECTOR_PORT:-9420}"
```

---

## Parity: Tauri-only or desktop-centric behavior

Things that differ when you **only** run Python (or deploy to the cloud):

| Area | Behavior | Notes |
|------|----------|--------|
| **SQLite + exports** | Default DB: `../data/director_cut.sqlite` relative to `backend/` (`DIRECTOR_DB` overrides). Exports under `backend/data/exports` (mounted at `/media/exports`). | A Fly **machine without a volume** starts empty and loses data on reschedule. Use a **[Fly volume](https://fly.io/docs/reference/volumes/)** (or external DB/object storage) if you need persistence. |
| **Bundled FFmpeg** | Tauri sets `DIRECTOR_RESOURCES_DIR` so `ffmpeg` resolution can prefer the app bundle. | In Docker/Linux, install `ffmpeg` on the image and rely on `PATH` (see Dockerfile below). |
| **OAuth desktop flow** | `oauth_redirect` in `/api/auth/config` defaults to `http://127.0.0.1:<DIRECTOR_PORT>/auth/callback`. | For a **public hostname**, you must add that URL in Supabase and align `DIRECTOR_PORT` / public URL expectations; PKCE verifier is **in-memory** (single concurrent flow per process). |
| **Desktop OAuth bridge** | `_desktop_oauth_bridge` globals — single-slot handoff between browser and app. | Fine for headless automation that does not rely on this path; irrelevant to MCP Bearer auth. |
| **Frontend URLs** | `src/main.js` uses hardcoded **`http://127.0.0.1:9420`** for EventSource, video URLs, etc. | Remote MCP does **not** change this. Changing the desktop UI to a remote base URL would be a **separate** product change. |

No code changes are strictly required for “API only on localhost” headless use if env and paths are correct.

Docker build/run and Fly deploy are covered in **[How to deploy (Docker → Fly.io)](#how-to-deploy-docker--flyio)** above.

---

## Security: what belongs in the runtime vs image

**Needed at runtime (secrets / config)**

- `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` — required for validating user JWTs against GoTrue (`/auth/v1/user`).
- Pipeline keys (from Settings DB or env): `GROQ_API_KEY`, `FAL_KEY` / `FAL_API_KEY`, optional model overrides.
- Optional: `DIRECTOR_DB`, `DIRECTOR_ENV_FILE`, `DIRECTOR_DESKTOP_MCP_SECRET` (stable MCP signing secret for HS256 automation tokens).
- `WMSTUDIO_PROJECT_ROOT`, `NEXT_PUBLIC_APP_URL` — only if you rely on layered `.env` or OAuth/wmstudio handoff.

**Never embed in the image**

- User refresh tokens, personal Supabase **service role** keys, or long-lived user access tokens.
- Private signing keys unrelated to the optional desktop MCP HS256 secret.

The anon key is **public by design** in many Supabase apps, but treat deployment values as sensitive configuration anyway (rotation, audit).

---

## Related

- [MCP_INTEGRATION.md](../MCP_INTEGRATION.md) — local MCP URL and auth notes
