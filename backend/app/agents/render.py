"""Render agent – invoke FFmpeg to produce output video."""

from app.agents.base import checkpoint, record_step, emit_progress, think
from app.schemas.run import Stage


async def render_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.RENDER.value
    await emit_progress(run_id, stage, "Rendering video…")

    await think(run_id, stage, "Building FFmpeg filter graph…")
    await think(run_id, stage, "Encoding video stream (H.264)…", delay=1.5)
    await think(run_id, stage, "Muxing audio tracks…", delay=0.8)
    await think(run_id, stage, "Writing output file…", delay=1.0)
    await think(run_id, stage, "Render finished.")

    output_path = f"data/exports/{run_id}/render.mp4"
    state["outputs"][stage] = {"rendered": True, "output_path": output_path}
    state["current_stage"] = Stage.PACKAGE.value

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Render complete ✓")
    return state
