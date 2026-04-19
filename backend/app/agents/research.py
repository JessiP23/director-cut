"""Research agent – gather background info for the production."""

from app.agents.base import checkpoint, record_step, emit_progress
from app.schemas.run import Stage
from app.services.llm import call_llm


async def research_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.RESEARCH.value
    await emit_progress(run_id, stage, "Researching topic…")

    research = await call_llm(
        system="You are a researcher. Given a production plan, identify key facts, references, and visual ideas. Output JSON.",
        user=str(state["outputs"].get("planning", {})),
        settings=state.get("settings", {}),
    )

    state["outputs"][stage] = research
    state["current_stage"] = Stage.SCRIPT.value

    await record_step(run_id, stage, "completed", research)
    await checkpoint(state, stage)
    return state
