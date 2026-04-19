"""Package agent – prepare multiple export presets."""

from app.agents.base import checkpoint, record_step, emit_progress, think
from app.schemas.run import Stage


async def package_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.PACKAGE.value
    await emit_progress(run_id, stage, "Packaging exports…")

    await think(run_id, stage, "Creating 1080p web preset…")
    await think(run_id, stage, "Creating 4K master preset…", delay=0.8)
    await think(run_id, stage, "Generating thumbnail…")
    await think(run_id, stage, "All presets packaged.")

    state["outputs"][stage] = {"packaged": True, "presets": ["1080p_web", "4k_master", "thumbnail"]}
    state["current_stage"] = Stage.EXPORT.value

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Packaging complete ✓")
    return state
