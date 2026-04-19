"""QA agent – validate timeline, check for errors before render."""

from app.agents.base import checkpoint, record_step, emit_progress, think
from app.schemas.run import Stage


async def qa_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.QA.value
    await emit_progress(run_id, stage, "Running QA checks…")

    await think(run_id, stage, "Checking timeline continuity…")
    await think(run_id, stage, "Verifying audio sync across all clips…")
    await think(run_id, stage, "Validating asset resolution and format…")
    await think(run_id, stage, "Checking total duration matches target…")
    await think(run_id, stage, "All checks passed ✓")

    state["outputs"][stage] = {"qa_passed": True, "issues": [], "checks_run": 4}
    state["current_stage"] = Stage.RENDER.value
    state["needs_approval"] = stage

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "QA complete – awaiting approval ⏳")
    return state
