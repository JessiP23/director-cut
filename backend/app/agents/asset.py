"""Asset agent – acquire or generate images, video clips, etc."""

from app.agents.base import checkpoint, record_step, emit_progress
from app.schemas.run import Stage


async def asset_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.ASSETS.value
    await emit_progress(run_id, stage, "Acquiring assets…")

    # Placeholder: real implementation would call image generation APIs,
    # download stock footage, etc.
    state["outputs"][stage] = {"assets_acquired": True, "count": 0}
    state["current_stage"] = Stage.AUDIO.value

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    return state
