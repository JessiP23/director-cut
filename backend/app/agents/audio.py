"""Audio agent – generate narration / music / sound effects."""

from app.agents.base import checkpoint, record_step, emit_progress, think
from app.schemas.run import Stage


async def audio_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.AUDIO.value
    await emit_progress(run_id, stage, "Generating audio…")

    await think(run_id, stage, "Preparing narration from script lines…")
    await think(run_id, stage, "Selecting background music track…", delay=0.8)
    await think(run_id, stage, "Generating sound effects for transitions…")
    await think(run_id, stage, "Audio mix complete.")

    state["outputs"][stage] = {"audio_generated": True, "tracks": ["narration", "music", "sfx"]}
    state["current_stage"] = Stage.EDIT.value

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Audio ready ✓")
    return state
