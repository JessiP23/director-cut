"""Artifact browsing routes."""
from __future__ import annotations


from fastapi import APIRouter
from app.db.repository import ArtifactRepository
from app.schemas.artifact import ArtifactOut

router = APIRouter()


@router.get("/{run_id}", response_model=list[ArtifactOut])
async def list_artifacts(run_id: str):
    repo = ArtifactRepository()
    return await repo.list_for_run(run_id)
