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
from typing import Dict, Optional

from app.schemas.run import Stage, RunStatus, RunCreate
from app.db.repository import CheckpointRepository
from app.db.connection import get_db
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
    # Persist the decision
    db = await get_db()
    await db.execute(
        "INSERT INTO approvals (id, run_id, stage, decision, notes) VALUES (?,?,?,?,?)",
        (uuid.uuid4().hex, run_id, stage, decision, notes),
    )
    await db.commit()
    await db.close()

    # Resolve the future the pipeline is awaiting
    fut = _approval_futures.pop(run_id, None)
    if fut and not fut.done():
        fut.set_result({"stage": stage, "decision": decision, "notes": notes})


async def cancel_run(run_id: str):
    task = _active_runs.pop(run_id, None)
    if task:
        task.cancel()
    db = await get_db()
    await db.execute(
        "UPDATE runs SET status='cancelled', updated_at=datetime('now') WHERE id=?",
        (run_id,),
    )
    await db.commit()
    await db.close()

# ── Pipeline driver ────────────────────────────────────────────────────────


async def _execute(run_id: str, state: dict):
    """Walk every stage in order, pausing at approval gates."""
    try:
        for stage in STAGE_ORDER:
            if state.get("cancelled"):
                break

            stage_name = stage.value
            node_fn = NODE_MAP[stage]

            # ── Update DB to current stage ──
            db = await get_db()
            await db.execute(
                "UPDATE runs SET current_stage=?, status='running', "
                "updated_at=datetime('now') WHERE id=?",
                (stage_name, run_id),
            )
            await db.commit()
            await db.close()

            # ── Emit "stage started" to SSE ──
            await event_bus.emit(run_id, "stage_start", {
                "stage": stage_name,
                "message": f"Starting {stage_name}…",
            })

            # ── Run the agent node (with timeout) ──
            # Render needs much longer for AI video generation
            stage_timeout = 1800 if stage == Stage.RENDER else 300
            try:
                state = await asyncio.wait_for(node_fn(state), timeout=stage_timeout)
            except asyncio.TimeoutError:
                raise RuntimeError(f"Stage {stage_name} timed out ({stage_timeout} s)")

            # ── Emit "stage complete" to SSE ──
            await event_bus.emit(run_id, "stage_complete", {
                "stage": stage_name,
                "message": f"{stage_name} complete ✓",
            })

            # ── Approval gate ──
            if stage in APPROVAL_GATES:
                await _wait_for_approval(run_id, stage_name, state)
                if state.get("cancelled"):
                    break

        # All stages done (or cancelled)
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
        db = await get_db()
        await db.execute(
            "UPDATE runs SET status='cancelled', updated_at=datetime('now') WHERE id=?",
            (run_id,),
        )
        await db.commit()
        await db.close()
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

    db = await get_db()
    await db.execute(
        "UPDATE runs SET status='awaiting_approval', "
        "updated_at=datetime('now') WHERE id=?",
        (run_id,),
    )
    await db.commit()
    await db.close()

    # Create a future the approval route will resolve
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
        # Auto-approve so the pipeline keeps moving during development
        log.warning("auto_approve_timeout", run_id=run_id, stage=stage_name)
        state["approvals"][stage_name] = {
            "decision": "approve",
            "notes": "auto-approved (5 min timeout)",
        }

    # Back to running
    db = await get_db()
    await db.execute(
        "UPDATE runs SET status='running', updated_at=datetime('now') WHERE id=?",
        (run_id,),
    )
    await db.commit()
    await db.close()


# ── Terminal helpers ───────────────────────────────────────────────────────


async def _finish_run(run_id: str, state: dict):
    db = await get_db()
    await db.execute(
        "UPDATE runs SET status='completed', current_stage='done', "
        "updated_at=datetime('now') WHERE id=?",
        (run_id,),
    )
    await db.commit()
    await db.close()
    await event_bus.emit(run_id, "run_completed", {
        "run_id": run_id,
        "message": "Production complete.",
    })
    await event_bus.close(run_id)


async def _fail_run(run_id: str, error: str):
    db = await get_db()
    await db.execute(
        "UPDATE runs SET status='failed', updated_at=datetime('now') WHERE id=?",
        (run_id,),
    )
    await db.execute(
        "INSERT INTO errors (id, run_id, message, recoverable) VALUES (?,?,?,1)",
        (uuid.uuid4().hex, run_id, error),
    )
    await db.commit()
    await db.close()
    await event_bus.emit(run_id, "run_failed", {
        "run_id": run_id,
        "error": error,
        "message": f"Run failed: {error}",
    })
    await event_bus.close(run_id)


async def _cancel_run_db(run_id: str):
    db = await get_db()
    await db.execute(
        "UPDATE runs SET status='cancelled', updated_at=datetime('now') WHERE id=?",
        (run_id,),
    )
    await db.commit()
    await db.close()
    await event_bus.emit(run_id, "run_cancelled", {
        "run_id": run_id,
        "message": "Run cancelled.",
    })
    await event_bus.close(run_id)
