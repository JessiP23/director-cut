"""Research agent – gather background info for the production."""

from app.agents.base import checkpoint, record_step, emit_progress, think
from app.schemas.run import Stage
from app.services.llm import call_llm


async def research_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.RESEARCH.value
    await emit_progress(run_id, stage, "Researching topic…")

    await think(run_id, stage, "Reviewing the production plan…")
    await think(run_id, stage, "Identifying key facts and references to research…")
    await think(run_id, stage, "Gathering visual inspiration and reference material…")

    research = await call_llm(
        system="You are a researcher. Given a production plan, identify key facts, references, and visual ideas. Output JSON with: facts[], references[], visual_ideas[].",
        user=str(state["outputs"].get("planning", {})),
        settings=state.get("settings", {}),
    )

    await think(run_id, stage, f"Found {len(research.get('facts', []))} facts, {len(research.get('visual_ideas', []))} visual ideas")

    state["outputs"][stage] = research
    state["current_stage"] = Stage.SCRIPT.value

    await record_step(run_id, stage, "completed", research)
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Research complete ✓")
    return state
