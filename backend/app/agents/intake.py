"""Intake node – parse and validate user prompt / brief."""

from app.agents.base import checkpoint, record_step, emit_progress
from app.schemas.run import Stage


async def intake_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.INTAKE.value
    await emit_progress(run_id, stage, "Parsing user prompt…")

    # Simple pass-through for now; a real implementation would
    # validate the prompt, extract intent, and detect attachments.
    state["outputs"][stage] = {
        "raw_prompt": state["prompt"],
        "parsed": True,
    }
    state["current_stage"] = Stage.PLANNING.value

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Intake complete.")
    return state
