"""Project CRUD routes."""
from __future__ import annotations


from fastapi import APIRouter, HTTPException
from app.db.repository import ProjectRepository
from app.schemas.project import ProjectCreate, ProjectOut

router = APIRouter()


@router.post("/", response_model=ProjectOut)
async def create_project(body: ProjectCreate):
    repo = ProjectRepository()
    project = await repo.create(body)
    return project


@router.get("/", response_model=list[ProjectOut])
async def list_projects():
    repo = ProjectRepository()
    return await repo.list_all()


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str):
    repo = ProjectRepository()
    project = await repo.get(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    repo = ProjectRepository()
    await repo.delete(project_id)
    return {"ok": True}
