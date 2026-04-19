"""App settings routes."""

from fastapi import APIRouter
from app.db.repository import SettingsRepository

router = APIRouter()


@router.get("/")
async def get_settings():
    repo = SettingsRepository()
    return await repo.get_all()


@router.put("/")
async def update_settings(body: dict):
    repo = SettingsRepository()
    await repo.upsert(body)
    return {"ok": True}
