"""Package agent – prepare multiple export presets."""

from app.agents.base import checkpoint, record_step, emit_progress
from app.schemas.run import Stage


async def package_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.PACKAGE.value
    await emit_progress(run_id, stage, "Packaging exports…")

    state["outputs"][stage] = {"packaged": True}
    state["current_stage"] = Stage.EXPORT.value

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    return state
