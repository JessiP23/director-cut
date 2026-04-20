"""FastAPI server – the HTTP surface that Tauri talks to."""

import os
from pathlib import Path

# Load .env before anything else reads env vars
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Director's Cut", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve rendered exports as static files (video playback)
_export_dir = Path(__file__).resolve().parent.parent / "data" / "exports"
_export_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media/exports", StaticFiles(directory=str(_export_dir)), name="exports")

# Import routes safely – each one handles its own import errors
from app.routes import projects, runs, artifacts, approvals, settings, events  # noqa: E402

app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(runs.router, prefix="/api/runs", tags=["runs"])
app.include_router(artifacts.router, prefix="/api/artifacts", tags=["artifacts"])
app.include_router(approvals.router, prefix="/api/approvals", tags=["approvals"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(events.router, prefix="/api/events", tags=["events"])


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.on_event("startup")
async def startup():
    """Initialize DB on startup and restore saved settings into env."""
    from app.db.connection import get_db
    db = await get_db()
    await db.close()
    # Restore saved settings into environment so agents use them
    try:
        from app.db.repository import SettingsRepository
        repo = SettingsRepository()
        saved = await repo.get_all()
        _env_map = {
            "groq_api_key": "GROQ_API_KEY",
            "fal_api_key": "FAL_KEY",
            "video_model": "FAL_VIDEO_MODEL",
            "ffmpeg_path": "FFMPEG_PATH",
            "model": "DIRECTOR_MODEL",
        }
        for skey, evar in _env_map.items():
            val = saved.get(skey)
            if val:
                os.environ[evar] = str(val)
        if saved.get("fal_api_key"):
            os.environ["FAL_API_KEY"] = str(saved["fal_api_key"])
    except Exception as e:
        print(f"⚠️ Could not restore settings: {e}")
    print("✅ Director's Cut backend ready on http://127.0.0.1:9420")
