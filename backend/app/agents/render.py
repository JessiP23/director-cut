"""Render agent – invoke FFmpeg to produce output video."""

from app.agents.base import checkpoint, record_step, emit_progress
from app.schemas.run import Stage


async def render_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.RENDER.value
    await emit_progress(run_id, stage, "Rendering video…")

    # Placeholder: real implementation calls FFmpeg via app.services.ffmpeg
    state["outputs"][stage] = {"rendered": True, "output_path": f"data/exports/{run_id}/render.mp4"}
    state["current_stage"] = Stage.PACKAGE.value

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    return state
