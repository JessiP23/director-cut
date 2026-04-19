"""Audio agent – generate narration / music / sound effects."""

from app.agents.base import checkpoint, record_step, emit_progress
from app.schemas.run import Stage


async def audio_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.AUDIO.value
    await emit_progress(run_id, stage, "Generating audio…")

    state["outputs"][stage] = {"audio_generated": True}
    state["current_stage"] = Stage.EDIT.value

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    return state
