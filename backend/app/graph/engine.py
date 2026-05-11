"""Sequential pipeline engine with approval gates and SSE streaming.

Each stage is an async function (agent node) that receives state → returns
state.  Approval gates pause the pipeline with an asyncio.Future that the
approval route resolves.  No external orchestration library required — this
is pure asyncio, which is the right tool for a linear pipeline.
"""
from __future__ import annotations

import asyncio
import json
import traceback
import uuid
from datetime import datetime
from typing import Dict, Optional

from app.schemas.run import Stage, RunStatus, RunCreate
from app.db.repository import CheckpointRepository
from app.db.connection import get_pool
from app.runtime.event_bus import event_bus
from app.runtime.logger import get_logger

from app.agents.intake import intake_node
from app.agents.planner import plan_node
from app.agents.research import research_node
from app.agents.script import script_node
from app.agents.storyboard import storyboard_node
from app.agents.asset import asset_node
from app.agents.audio import audio_node
from app.agents.edit import edit_node
from app.agents.qa import qa_node
from app.agents.render import render_node
from app.agents.package import package_node
from app.agents.export import export_node

log = get_logger("engine")

# ── Stage config ────────────────────────────────────────────────────────────

STAGE_ORDER = [
    Stage.INTAKE, Stage.PLANNING, Stage.RESEARCH, Stage.SCRIPT,
    Stage.STORYBOARD, Stage.ASSETS, Stage.AUDIO, Stage.EDIT,
    Stage.QA, Stage.RENDER, Stage.PACKAGE, Stage.EXPORT,
]

APPROVAL_GATES: set = set()  # disabled — pipeline runs straight through to render

NODE_MAP = {
    Stage.INTAKE: intake_node,
    Stage.PLANNING: plan_node,
    Stage.RESEARCH: research_node,
    Stage.SCRIPT: script_node,
    Stage.STORYBOARD: storyboard_node,
    Stage.ASSETS: asset_node,
    Stage.AUDIO: audio_node,
    Stage.EDIT: edit_node,
    Stage.QA: qa_node,
    Stage.RENDER: render_node,
    Stage.PACKAGE: package_node,
    Stage.EXPORT: export_node,
}

# ── In-memory bookkeeping ──────────────────────────────────────────────────

_active_runs: Dict[str, asyncio.Task] = {}
_approval_futures: Dict[str, asyncio.Future] = {}

# ── Public API (called from routes) ────────────────────────────────────────


async def start_run_async(run_id: str, body: RunCreate):
    """Fire-and-forget: launch the pipeline as a background task."""
    initial_state = {
        "run_id": run_id,
        "project_id": body.project_id,
        "prompt": body.prompt,
        "status": RunStatus.RUNNING,
        "current_stage": Stage.INTAKE.value,
        "outputs": {},
        "artifact_ids": [],
        "approvals": {},
        "errors": [],
        "settings": body.settings or {},
        "cancelled": False,
        "needs_approval": None,
    }
    task = asyncio.create_task(_execute(run_id, initial_state))
    _active_runs[run_id] = task


async def submit_approval(run_id: str, stage: str, decision: str, notes: str = ""):
    """Called by the approval route to unblock a paused pipeline."""
    pool = get_pool()
    now = datetime.utcnow().isoformat()
    await pool.execute(
        "INSERT INTO approvals (id, run_id, stage, decision, notes, created_at)"
        " VALUES ($1, $2, $3, $4, $5, $6)",
        uuid.uuid4().hex, run_id, stage, decision, notes, now,
    )

    fut = _approval_futures.pop(run_id, None)
    if fut and not fut.done():
        fut.set_result({"stage": stage, "decision": decision, "notes": notes})


def pipeline_task_active(run_id: str) -> bool:
    """True when the asyncio pipeline driver is actively running this run."""
    return run_id in _active_runs


async def cancel_run(run_id: str):
    task = _active_runs.pop(run_id, None)
    if task:
        task.cancel()
    pool = get_pool()
    now = datetime.utcnow().isoformat()
    await pool.execute(
        "UPDATE runs SET status='cancelled', updated_at=$1 WHERE id=$2",
        now, run_id,
    )

# ── Pipeline driver ────────────────────────────────────────────────────────


async def _execute(run_id: str, state: dict):
    """Walk every stage in order, pausing at approval gates."""
    try:
        for stage in STAGE_ORDER:
            if state.get("cancelled"):
                break

            stage_name = stage.value
            node_fn = NODE_MAP[stage]

            pool = get_pool()
            now = datetime.utcnow().isoformat()
            await pool.execute(
                "UPDATE runs SET current_stage=$1, status='running', updated_at=$2 WHERE id=$3",
                stage_name, now, run_id,
            )

            await event_bus.emit(run_id, "stage_start", {
                "stage": stage_name,
                "message": f"Starting {stage_name}…",
            })

            stage_timeout = 1800 if stage == Stage.RENDER else 300
            try:
                state = await asyncio.wait_for(node_fn(state), timeout=stage_timeout)
            except asyncio.TimeoutError:
                raise RuntimeError(f"Stage {stage_name} timed out ({stage_timeout} s)")

            await event_bus.emit(run_id, "stage_complete", {
                "stage": stage_name,
                "message": f"{stage_name} complete ✓",
            })

            if stage in APPROVAL_GATES:
                await _wait_for_approval(run_id, stage_name, state)
                if state.get("cancelled"):
                    break

        if state.get("cancelled"):
            await _cancel_run_db(run_id)
        else:
            await _finish_run(run_id, state)

    except asyncio.CancelledError:
        log.info("run_cancelled", run_id=run_id)
        await event_bus.emit(run_id, "run_cancelled", {
            "stage": "interrupt",
            "message": "Run cancelled by user.",
        })
        pool = get_pool()
        now = datetime.utcnow().isoformat()
        await pool.execute(
            "UPDATE runs SET status='cancelled', updated_at=$1 WHERE id=$2",
            now, run_id,
        )
    except Exception as exc:
        log.error("run_failed", run_id=run_id, error=str(exc))
        traceback.print_exc()
        await _fail_run(run_id, str(exc))
    finally:
        _active_runs.pop(run_id, None)
        _approval_futures.pop(run_id, None)


async def _wait_for_approval(run_id: str, stage_name: str, state: dict):
    """Pause pipeline, emit SSE event, wait for approval future."""
    await event_bus.emit(run_id, "stage_progress", {
        "stage": stage_name,
        "message": f"{stage_name} – awaiting approval",
    })

    pool = get_pool()
    now = datetime.utcnow().isoformat()
    await pool.execute(
        "UPDATE runs SET status='awaiting_approval', updated_at=$1 WHERE id=$2",
        now, run_id,
    )

    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    _approval_futures[run_id] = fut

    try:
        result = await asyncio.wait_for(fut, timeout=300)
        state["approvals"][stage_name] = result
        if result.get("decision") == "reject":
            state["cancelled"] = True
            return
    except asyncio.TimeoutError:
        log.warning("auto_approve_timeout", run_id=run_id, stage=stage_name)
        state["approvals"][stage_name] = {
            "decision": "approve",
            "notes": "auto-approved (5 min timeout)",
        }

    now2 = datetime.utcnow().isoformat()
    await pool.execute(
        "UPDATE runs SET status='running', updated_at=$1 WHERE id=$2",
        now2, run_id,
    )


# ── Terminal helpers ───────────────────────────────────────────────────────


async def _finish_run(run_id: str, state: dict):
    pool = get_pool()
    now = datetime.utcnow().isoformat()
    await pool.execute(
        "UPDATE runs SET status='completed', current_stage='done', updated_at=$1 WHERE id=$2",
        now, run_id,
    )
    await event_bus.emit(run_id, "run_completed", {
        "run_id": run_id,
        "message": "Production complete.",
    })
    await event_bus.close(run_id)


async def _fail_run(run_id: str, error: str):
    pool = get_pool()
    now = datetime.utcnow().isoformat()
    await pool.execute(
        "UPDATE runs SET status='failed', updated_at=$1 WHERE id=$2",
        now, run_id,
    )
    await pool.execute(
        "INSERT INTO errors (id, run_id, message, recoverable, created_at)"
        " VALUES ($1, $2, $3, 1, $4)",
        uuid.uuid4().hex, run_id, error, now,
    )
    await event_bus.emit(run_id, "run_failed", {
        "run_id": run_id,
        "error": error,
        "message": f"Run failed: {error}",
    })
    await event_bus.close(run_id)


async def _cancel_run_db(run_id: str):
    pool = get_pool()
    now = datetime.utcnow().isoformat()
    await pool.execute(
        "UPDATE runs SET status='cancelled', updated_at=$1 WHERE id=$2",
        now, run_id,
    )
    await event_bus.emit(run_id, "run_cancelled", {
        "run_id": run_id,
        "message": "Run cancelled.",
    })
    await event_bus.close(run_id)
