"""Run management routes."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException
from app.schemas.run import RunCreate, RunOut, RunStatus, Stage

router = APIRouter()


@router.post("/", response_model=RunOut)
async def create_run(body: RunCreate):
    from app.db.connection import get_db
    run_id = uuid.uuid4().hex
    db = await get_db()
    now = datetime.utcnow().isoformat()
    await db.execute(
        "INSERT INTO runs (id, project_id, prompt, status, current_stage, settings_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (run_id, body.project_id, body.prompt, "running", "intake", json.dumps(body.settings), now, now),
    )
    await db.commit()
    await db.close()

    # Start the pipeline (non-blocking background task)
    try:
        from app.graph.engine import start_run_async
        asyncio.create_task(start_run_async(run_id, body))
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"⚠️ Pipeline engine failed to start: {e}")

    return RunOut(
        id=run_id, project_id=body.project_id, prompt=body.prompt,
        status=RunStatus.RUNNING, current_stage=Stage.INTAKE, created_at=now, updated_at=now,
    )


@router.get("/", response_model=list[RunOut])
async def list_runs():
    from app.db.connection import get_db
    db = await get_db()
    cursor = await db.execute("SELECT * FROM runs ORDER BY created_at DESC")
    rows = await cursor.fetchall()
    await db.close()
    return [
        RunOut(id=r["id"], project_id=r["project_id"], prompt=r["prompt"],
               status=r["status"], current_stage=r["current_stage"],
               created_at=r["created_at"], updated_at=r["updated_at"])
        for r in rows
    ]


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: str):
    from app.db.connection import get_db
    db = await get_db()
    cursor = await db.execute("SELECT * FROM runs WHERE id=?", (run_id,))
    row = await cursor.fetchone()
    await db.close()
    if not row:
        raise HTTPException(404, "Run not found")
    return RunOut(
        id=row["id"], project_id=row["project_id"], prompt=row["prompt"],
        status=row["status"], current_stage=row["current_stage"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


@router.post("/{run_id}/cancel")
async def cancel(run_id: str):
    from app.db.connection import get_db
    db = await get_db()
    await db.execute("UPDATE runs SET status='cancelled', updated_at=datetime('now') WHERE id=?", (run_id,))
    await db.commit()
    await db.close()
    return {"ok": True}


@router.post("/{run_id}/resume")
async def resume(run_id: str):
    return {"ok": True, "message": "Resume not yet implemented"}
