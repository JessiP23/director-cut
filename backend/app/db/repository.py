"""Data-access repositories for all Postgres tables (asyncpg)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional

from app.db.connection import get_pool
from app.schemas.project import ProjectCreate, ProjectOut
from app.schemas.artifact import ArtifactOut


def _now() -> str:
    return datetime.utcnow().isoformat()


class ProjectRepository:
    async def create(self, body: ProjectCreate) -> ProjectOut:
        pool = get_pool()
        pid = uuid.uuid4().hex
        now = _now()
        await pool.execute(
            "INSERT INTO projects (id, name, description, created_at, updated_at)"
            " VALUES ($1, $2, $3, $4, $5)",
            pid, body.name, body.description, now, now,
        )
        return ProjectOut(id=pid, name=body.name, description=body.description,
                          created_at=now, updated_at=now)

    async def list_all(self) -> list[ProjectOut]:
        pool = get_pool()
        rows = await pool.fetch("SELECT * FROM projects ORDER BY created_at DESC")
        return [
            ProjectOut(id=r["id"], name=r["name"], description=r["description"],
                       created_at=r["created_at"], updated_at=r["updated_at"])
            for r in rows
        ]

    async def get(self, project_id: str) -> Optional[ProjectOut]:
        pool = get_pool()
        r = await pool.fetchrow("SELECT * FROM projects WHERE id = $1", project_id)
        if not r:
            return None
        return ProjectOut(id=r["id"], name=r["name"], description=r["description"],
                          created_at=r["created_at"], updated_at=r["updated_at"])

    async def delete(self, project_id: str) -> None:
        pool = get_pool()
        await pool.execute("DELETE FROM projects WHERE id = $1", project_id)


class ArtifactRepository:
    async def list_for_run(self, run_id: str) -> list[ArtifactOut]:
        pool = get_pool()
        rows = await pool.fetch(
            "SELECT * FROM artifacts WHERE run_id = $1 ORDER BY created_at", run_id
        )
        return [
            ArtifactOut(
                id=r["id"], run_id=r["run_id"], stage=r["stage"], kind=r["kind"],
                path=r["path"], version=r["version"],
                metadata=json.loads(r["metadata_json"]), created_at=r["created_at"],
            )
            for r in rows
        ]

    async def save(
        self,
        run_id: str,
        stage: str,
        kind: str,
        path: str,
        metadata: Optional[dict] = None,
        version: int = 1,
    ) -> str:
        pool = get_pool()
        aid = uuid.uuid4().hex
        now = _now()
        await pool.execute(
            "INSERT INTO artifacts"
            " (id, run_id, stage, kind, path, version, metadata_json, created_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            aid, run_id, stage, kind, path, version, json.dumps(metadata or {}), now,
        )
        return aid


class SettingsRepository:
    async def get_all(self) -> dict:
        pool = get_pool()
        rows = await pool.fetch("SELECT key, value_json FROM settings")
        return {r["key"]: json.loads(r["value_json"]) for r in rows}

    async def upsert(self, data: dict) -> None:
        pool = get_pool()
        now = _now()
        for key, value in data.items():
            await pool.execute(
                "INSERT INTO settings (key, value_json, updated_at) VALUES ($1, $2, $3)"
                " ON CONFLICT (key)"
                " DO UPDATE SET value_json = EXCLUDED.value_json,"
                "               updated_at = EXCLUDED.updated_at",
                key, json.dumps(value), now,
            )


class CheckpointRepository:
    async def save(self, run_id: str, stage: str, state: dict) -> None:
        pool = get_pool()
        cid = uuid.uuid4().hex
        now = _now()
        await pool.execute(
            "INSERT INTO checkpoints (id, run_id, stage, state_json, created_at)"
            " VALUES ($1, $2, $3, $4, $5)",
            cid, run_id, stage, json.dumps(state), now,
        )

    async def latest(self, run_id: str) -> Optional[dict]:
        pool = get_pool()
        row = await pool.fetchrow(
            "SELECT state_json FROM checkpoints"
            " WHERE run_id = $1 ORDER BY created_at DESC LIMIT 1",
            run_id,
        )
        if row:
            return json.loads(row["state_json"])
        return None
