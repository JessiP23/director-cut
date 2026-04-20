"""App settings routes."""
from __future__ import annotations

import os

from fastapi import APIRouter
from app.db.repository import SettingsRepository

router = APIRouter()

# Keys that map to environment variables so agents pick them up at runtime
_ENV_MAP = {
    "groq_api_key": "GROQ_API_KEY",
    "fal_api_key": "FAL_KEY",
    "video_model": "FAL_VIDEO_MODEL",
    "ffmpeg_path": "FFMPEG_PATH",
    "model": "DIRECTOR_MODEL",
}


@router.get("/")
async def get_settings():
    repo = SettingsRepository()
    return await repo.get_all()


@router.put("/")
async def update_settings(body: dict):
    repo = SettingsRepository()
    await repo.upsert(body)
    # Push relevant keys into env so agents use them immediately
    for setting_key, env_var in _ENV_MAP.items():
        val = body.get(setting_key)
        if val:
            os.environ[env_var] = str(val)
    # Also set FAL_API_KEY alias
    if body.get("fal_api_key"):
        os.environ["FAL_API_KEY"] = str(body["fal_api_key"])
    return {"ok": True}
