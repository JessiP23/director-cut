"""Storyboard agent – produce visual shot list / storyboard."""

from app.agents.base import checkpoint, record_step, emit_progress
from app.schemas.run import Stage
from app.services.llm import call_llm


async def storyboard_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.STORYBOARD.value
    await emit_progress(run_id, stage, "Creating storyboard…")

    storyboard = await call_llm(
        system="You are a storyboard artist. Given a script, output a shot list with visual descriptions, camera angles, and transitions in JSON.",
        user=str(state["outputs"].get("script", {})),
        settings=state.get("settings", {}),
    )

    state["outputs"][stage] = storyboard
    state["current_stage"] = Stage.ASSETS.value
    state["needs_approval"] = stage

    await record_step(run_id, stage, "completed", storyboard)
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Storyboard ready – awaiting approval.")
    return state
