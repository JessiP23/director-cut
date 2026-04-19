"""Data-access repositories for all SQLite tables."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional, List

from app.db.connection import get_db
from app.schemas.project import ProjectCreate, ProjectOut
from app.schemas.artifact import ArtifactOut


class ProjectRepository:
    async def create(self, body: ProjectCreate) -> ProjectOut:
        db = await get_db()
        pid = uuid.uuid4().hex
        now = datetime.utcnow().isoformat()
        await db.execute(
            "INSERT INTO projects (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
            (pid, body.name, body.description, now, now),
        )
        await db.commit()
        await db.close()
        return ProjectOut(id=pid, name=body.name, description=body.description, created_at=now, updated_at=now)

    async def list_all(self) -> list[ProjectOut]:
        db = await get_db()
        cursor = await db.execute("SELECT * FROM projects ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        await db.close()
        return [ProjectOut(id=r["id"], name=r["name"], description=r["description"],
                           created_at=r["created_at"], updated_at=r["updated_at"]) for r in rows]

    async def get(self, project_id: str) -> Optional[ProjectOut]:
        db = await get_db()
        cursor = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        r = await cursor.fetchone()
        await db.close()
        if not r:
            return None
        return ProjectOut(id=r["id"], name=r["name"], description=r["description"],
                          created_at=r["created_at"], updated_at=r["updated_at"])

    async def delete(self, project_id: str):
        db = await get_db()
        await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        await db.commit()
        await db.close()


class ArtifactRepository:
    async def list_for_run(self, run_id: str) -> list[ArtifactOut]:
        db = await get_db()
        cursor = await db.execute("SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at", (run_id,))
        rows = await cursor.fetchall()
        await db.close()
        return [
            ArtifactOut(
                id=r["id"], run_id=r["run_id"], stage=r["stage"], kind=r["kind"],
                path=r["path"], version=r["version"],
                metadata=json.loads(r["metadata_json"]), created_at=r["created_at"],
            )
            for r in rows
        ]

    async def save(self, run_id: str, stage: str, kind: str, path: str, metadata: Optional[dict] = None, version: int = 1):
        db = await get_db()
        aid = uuid.uuid4().hex
        await db.execute(
            "INSERT INTO artifacts (id, run_id, stage, kind, path, version, metadata_json) VALUES (?,?,?,?,?,?,?)",
            (aid, run_id, stage, kind, path, version, json.dumps(metadata or {})),
        )
        await db.commit()
        await db.close()
        return aid


class SettingsRepository:
    async def get_all(self) -> dict:
        db = await get_db()
        cursor = await db.execute("SELECT key, value_json FROM settings")
        rows = await cursor.fetchall()
        await db.close()
        return {r["key"]: json.loads(r["value_json"]) for r in rows}

    async def upsert(self, data: dict):
        db = await get_db()
        now = datetime.utcnow().isoformat()
        for key, value in data.items():
            await db.execute(
                "INSERT INTO settings (key, value_json, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
                (key, json.dumps(value), now),
            )
        await db.commit()
        await db.close()


class CheckpointRepository:
    async def save(self, run_id: str, stage: str, state: dict):
        db = await get_db()
        cid = uuid.uuid4().hex
        await db.execute(
            "INSERT INTO checkpoints (id, run_id, stage, state_json) VALUES (?,?,?,?)",
            (cid, run_id, stage, json.dumps(state)),
        )
        await db.commit()
        await db.close()

    async def latest(self, run_id: str) -> Optional[dict]:
        db = await get_db()
        cursor = await db.execute(
            "SELECT state_json FROM checkpoints WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        )
        row = await cursor.fetchone()
        await db.close()
        if row:
            return json.loads(row["state_json"])
        return None
