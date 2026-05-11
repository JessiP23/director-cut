"""Base helpers shared across all agent nodes."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Optional

from app.db.connection import get_pool
from app.db.repository import CheckpointRepository
from app.runtime.event_bus import event_bus
from app.runtime.logger import get_logger


async def checkpoint(state: dict, stage: str):
    """Persist current state to Postgres."""
    repo = CheckpointRepository()
    await repo.save(state["run_id"], stage, state)


async def record_step(
    run_id: str,
    stage: str,
    status: str,
    output: Optional[dict] = None,
    error: Optional[str] = None,
):
    pool = get_pool()
    now = datetime.utcnow().isoformat()
    await pool.execute(
        "INSERT INTO run_steps (id, run_id, stage, status, output_json, error, started_at)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7)",
        uuid.uuid4().hex, run_id, stage, status, json.dumps(output or {}), error, now,
    )


async def emit_progress(run_id: str, stage: str, message: str):
    await event_bus.emit(run_id, "stage_progress", {"stage": stage, "message": message})


async def think(run_id: str, stage: str, thought: str, delay: float = 0.6):
    """Emit a 'thinking' message with a small delay for realistic pacing."""
    await event_bus.emit(run_id, "stage_thinking", {"stage": stage, "message": thought})
    await asyncio.sleep(delay)
