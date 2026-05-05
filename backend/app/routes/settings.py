"""App settings routes."""
from __future__ import annotations

import os

from fastapi import APIRouter
from app.db.repository import SettingsRepository

router = APIRouter()

# Persisted preferences that sync into env when saved (see PUT /api/settings).
_ENV_MAP = {
    "video_model": "FAL_VIDEO_MODEL",
}


@router.get("/")
async def get_settings():
    repo = SettingsRepository()
    stored = await repo.get_all()

    groq_saved = str(stored.get("groq_api_key") or "").strip()
    fal_saved = str(stored.get("fal_api_key") or "").strip()
    groq_env = str(os.getenv("GROQ_API_KEY") or "").strip()
    fal_env = (
        str(os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or "").strip()
    )

    groq_eff = groq_saved or groq_env
    fal_eff = fal_saved or fal_env

    out = {
        k: v
        for k, v in stored.items()
        if k not in ("groq_api_key", "fal_api_key", "model", "ffmpeg_path")
    }
    # Never expose API key material — only flags for the UI
    out["groq_api_key"] = ""
    out["fal_api_key"] = ""
    out["groq_configured"] = bool(groq_eff)
    out["fal_configured"] = bool(fal_eff)
    out["groq_embedded"] = bool(groq_env and not groq_saved)
    out["fal_embedded"] = bool(fal_env and not fal_saved)
    return out


@router.put("/")
async def update_settings(body: dict):
    repo = SettingsRepository()

    merged = dict(body)
    merged.pop("groq_api_key", None)
    merged.pop("fal_api_key", None)
    merged.pop("model", None)
    merged.pop("ffmpeg_path", None)

    if merged:
        await repo.upsert(merged)

    stored = await repo.get_all()

    for setting_key, env_var in _ENV_MAP.items():
        candidate = merged.get(setting_key)
        if candidate is None:
            candidate = stored.get(setting_key)
        if candidate is not None and str(candidate).strip() != "":
            os.environ[env_var] = str(candidate)

    fk = stored.get("fal_api_key")
    if fk and str(fk).strip():
        os.environ["FAL_KEY"] = str(fk)
        os.environ["FAL_API_KEY"] = str(fk)

    gk = stored.get("groq_api_key")
    if gk and str(gk).strip():
        os.environ["GROQ_API_KEY"] = str(gk)

    return {"ok": True}
