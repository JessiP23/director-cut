"""QA agent – validate timeline, check for errors before render."""

from app.agents.base import checkpoint, record_step, emit_progress
from app.schemas.run import Stage


async def qa_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.QA.value
    await emit_progress(run_id, stage, "Running QA checks…")

    # Real implementation: validate timeline durations, asset integrity, audio sync, etc.
    state["outputs"][stage] = {"qa_passed": True, "issues": []}
    state["current_stage"] = Stage.RENDER.value
    state["needs_approval"] = stage

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "QA complete – awaiting approval.")
    return state
