"""LangGraph pipeline definition and execution engine."""
from __future__ import annotations


import asyncio
import json
import uuid
from typing import Any

from langgraph.graph import StateGraph, END

from app.schemas.state import PipelineState
from app.schemas.run import Stage, RunStatus, RunCreate, RunOut
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

# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

STAGE_ORDER = [
    Stage.INTAKE, Stage.PLANNING, Stage.RESEARCH, Stage.SCRIPT,
    Stage.STORYBOARD, Stage.ASSETS, Stage.AUDIO, Stage.EDIT,
    Stage.QA, Stage.RENDER, Stage.PACKAGE, Stage.EXPORT,
]

APPROVAL_GATES = {Stage.SCRIPT, Stage.STORYBOARD, Stage.EDIT, Stage.QA}


def _next_stage(stage: Stage) -> Stage:
    idx = STAGE_ORDER.index(stage)
    if idx + 1 < len(STAGE_ORDER):
        return STAGE_ORDER[idx + 1]
    return Stage.DONE


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


def _should_pause(state: dict) -> str:
    """Router: check if we need approval before advancing."""
    stage = Stage(state["current_stage"])
    if stage in APPROVAL_GATES and state.get("needs_approval"):
        return "await_approval"
    return "continue"


async def _await_approval_node(state: dict) -> dict:
    state["status"] = RunStatus.AWAITING_APPROVAL
    return state


def build_graph() -> StateGraph:
    graph = StateGraph(dict)

    # Add every stage node
    for stage, fn in NODE_MAP.items():
        graph.add_node(stage.value, fn)

    graph.add_node("await_approval", _await_approval_node)

    # Set entry
    graph.set_entry_point(Stage.INTAKE.value)

    # Linear edges with approval gate routing
    for i, stage in enumerate(STAGE_ORDER):
        if stage in APPROVAL_GATES:
            graph.add_conditional_edges(
                stage.value,
                _should_pause,
                {"await_approval": "await_approval", "continue": _next_stage(stage).value if _next_stage(stage) != Stage.DONE else END},
            )
            # After approval, go to next stage
            nxt = _next_stage(stage)
            graph.add_edge("await_approval", nxt.value if nxt != Stage.DONE else END)
        else:
            nxt = _next_stage(stage)
            graph.add_edge(stage.value, nxt.value if nxt != Stage.DONE else END)

    return graph


_compiled = build_graph().compile()

# ---------------------------------------------------------------------------
# Active runs (in-memory handles)
# ---------------------------------------------------------------------------

_active_runs: dict[str, asyncio.Task] = {}


async def start_run(body: RunCreate) -> RunOut:
    run_id = uuid.uuid4().hex
    db = await get_db()
    await db.execute(
        "INSERT INTO runs (id, project_id, prompt, status, current_stage, settings_json) VALUES (?,?,?,?,?,?)",
        (run_id, body.project_id, body.prompt, "running", "intake", json.dumps(body.settings)),
    )
    await db.commit()
    await db.close()

    initial_state = {
        "run_id": run_id,
        "project_id": body.project_id,
        "prompt": body.prompt,
        "status": RunStatus.RUNNING,
        "current_stage": Stage.INTAKE,
        "outputs": {},
        "artifact_ids": [],
        "approvals": {},
        "errors": [],
        "settings": body.settings,
        "cancelled": False,
        "needs_approval": None,
    }

    task = asyncio.create_task(_execute(run_id, initial_state))
    _active_runs[run_id] = task

    return RunOut(id=run_id, project_id=body.project_id, prompt=body.prompt, status=RunStatus.RUNNING)


async def _execute(run_id: str, state: dict):
    try:
        result = await _compiled.ainvoke(state)
        await _finish_run(run_id, result)
    except Exception as exc:
        log.error("run_failed", run_id=run_id, error=str(exc))
        await _fail_run(run_id, str(exc))


async def _finish_run(run_id: str, state: dict):
    db = await get_db()
    await db.execute("UPDATE runs SET status='completed', current_stage='done', updated_at=datetime('now') WHERE id=?", (run_id,))
    await db.commit()
    await db.close()
    await event_bus.emit(run_id, "run_completed", {"run_id": run_id})
    await event_bus.close(run_id)


async def _fail_run(run_id: str, error: str):
    db = await get_db()
    await db.execute("UPDATE runs SET status='failed', updated_at=datetime('now') WHERE id=?", (run_id,))
    await db.execute("INSERT INTO errors (id, run_id, message, recoverable) VALUES (?,?,?,1)",
                     (uuid.uuid4().hex, run_id, error))
    await db.commit()
    await db.close()
    await event_bus.emit(run_id, "run_failed", {"run_id": run_id, "error": error})
    await event_bus.close(run_id)


async def get_run_status(run_id: str) -> RunOut | None:
    db = await get_db()
    cursor = await db.execute("SELECT * FROM runs WHERE id=?", (run_id,))
    row = await cursor.fetchone()
    await db.close()
    if not row:
        return None
    return RunOut(
        id=row["id"], project_id=row["project_id"], prompt=row["prompt"],
        status=row["status"], current_stage=row["current_stage"],
    )


async def cancel_run(run_id: str):
    task = _active_runs.get(run_id)
    if task:
        task.cancel()
    db = await get_db()
    await db.execute("UPDATE runs SET status='cancelled', updated_at=datetime('now') WHERE id=?", (run_id,))
    await db.commit()
    await db.close()


async def resume_run(run_id: str):
    repo = CheckpointRepository()
    state = await repo.latest(run_id)
    if not state:
        raise ValueError(f"No checkpoint found for run {run_id}")
    task = asyncio.create_task(_execute(run_id, state))
    _active_runs[run_id] = task


async def submit_approval(run_id: str, action):
    """Unblock a paused run after user approves/rejects."""
    db = await get_db()
    await db.execute(
        "INSERT INTO approvals (id, run_id, stage, decision, notes) VALUES (?,?,?,?,?)",
        (uuid.uuid4().hex, run_id, action.stage, action.decision, action.notes),
    )
    await db.commit()
    await db.close()
    # In a real implementation, this would signal the paused graph node.
    # For now, we resume the run with the approval recorded.
    await resume_run(run_id)
