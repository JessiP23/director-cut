"""Intake node – parse and validate user prompt / brief."""

from app.agents.base import checkpoint, record_step, emit_progress, think
from app.schemas.run import Stage


async def intake_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.INTAKE.value
    await emit_progress(run_id, stage, "Parsing user prompt…")

    await think(run_id, stage, "Reading the creative brief…")
    await think(run_id, stage, "Identifying key themes and intent…")
    await think(run_id, stage, f"Prompt: \"{state['prompt'][:100]}\"")
    await think(run_id, stage, "Validating input — looks good, moving to planning.")

    state["outputs"][stage] = {
        "raw_prompt": state["prompt"],
        "parsed": True,
    }
    state["current_stage"] = Stage.PLANNING.value

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Intake complete ✓")
    return state
