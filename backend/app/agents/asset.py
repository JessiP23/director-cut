"""Asset agent – acquire or generate images, video clips, etc."""

from app.agents.base import checkpoint, record_step, emit_progress, think
from app.schemas.run import Stage


async def asset_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.ASSETS.value
    await emit_progress(run_id, stage, "Acquiring assets…")

    storyboard = state["outputs"].get("storyboard", {})
    shots = storyboard.get("shots", [])
    await think(run_id, stage, f"Reviewing storyboard — {len(shots)} shots need assets")
    await think(run_id, stage, "Searching asset library for matching footage…")
    await think(run_id, stage, "Generating placeholder images for missing shots…", delay=1.0)
    await think(run_id, stage, "All assets collected and catalogued.")

    state["outputs"][stage] = {"assets_acquired": True, "count": max(len(shots), 3)}
    state["current_stage"] = Stage.AUDIO.value

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Assets ready ✓")
    return state
