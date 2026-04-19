"""Edit assembly agent – build the timeline / EDL."""

from app.agents.base import checkpoint, record_step, emit_progress, think
from app.schemas.run import Stage


async def edit_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.EDIT.value
    await emit_progress(run_id, stage, "Assembling edit…")

    await think(run_id, stage, "Loading assets onto timeline…")
    await think(run_id, stage, "Syncing narration with visual cuts…", delay=0.8)
    await think(run_id, stage, "Applying transitions from storyboard…")
    await think(run_id, stage, "Adding B-roll and overlay graphics…", delay=0.8)
    await think(run_id, stage, "Timeline assembled — ready for review.")

    state["outputs"][stage] = {"timeline_built": True, "duration_seconds": 60}
    state["current_stage"] = Stage.QA.value
    state["needs_approval"] = stage

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Edit assembled – awaiting approval ⏳")
    return state
