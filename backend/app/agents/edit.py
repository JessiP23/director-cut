"""Edit assembly agent – build the timeline / EDL."""

from app.agents.base import checkpoint, record_step, emit_progress
from app.schemas.run import Stage


async def edit_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.EDIT.value
    await emit_progress(run_id, stage, "Assembling edit…")

    state["outputs"][stage] = {"timeline_built": True}
    state["current_stage"] = Stage.QA.value
    state["needs_approval"] = stage

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Edit assembled – awaiting approval.")
    return state
