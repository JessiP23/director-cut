"""Planner agent – produce a structured production plan."""

from app.agents.base import checkpoint, record_step, emit_progress
from app.schemas.run import Stage
from app.services.llm import call_llm


async def plan_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.PLANNING.value
    await emit_progress(run_id, stage, "Generating production plan…")

    plan = await call_llm(
        system="You are a video production planner. Given a prompt, output a structured JSON plan with: title, target_length_seconds, tone, style, scenes[].",
        user=state["prompt"],
        settings=state.get("settings", {}),
    )

    state["outputs"][stage] = plan
    state["current_stage"] = Stage.RESEARCH.value

    await record_step(run_id, stage, "completed", plan)
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Plan ready.")
    return state
