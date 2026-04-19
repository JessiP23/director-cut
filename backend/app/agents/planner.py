"""Planner agent – produce a structured production plan."""

from app.agents.base import checkpoint, record_step, emit_progress, think
from app.schemas.run import Stage
from app.services.llm import call_llm


def _resolve_max_scenes(state: dict) -> int:
    raw = (state.get("settings") or {}).get("max_scenes", 4)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 4
    return max(1, min(value, 8))


async def plan_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.PLANNING.value
    await emit_progress(run_id, stage, "Generating production plan…")

    await think(run_id, stage, "Analyzing the creative brief for scope and tone…")
    await think(run_id, stage, "Determining target length, style, and structure…")
    await think(run_id, stage, "Calling LLM to generate structured plan…")

    max_scenes = _resolve_max_scenes(state)

    plan = await call_llm(
        system=(
            "You are a video production planner. Given a prompt, output a structured JSON plan "
            "with: title, target_length_seconds, tone, style, scenes[]. "
            f"Return at most {max_scenes} scenes."
        ),
        user=state["prompt"],
        settings=state.get("settings", {}),
    )

    scenes = plan.get("scenes", [])
    if isinstance(scenes, list) and len(scenes) > max_scenes:
        plan["scenes"] = scenes[:max_scenes]
        await think(run_id, stage, f"Trimmed plan to max scenes: {max_scenes}")

    await think(run_id, stage, f"Plan generated: {plan.get('title', 'Untitled')} — {len(plan.get('scenes', []))} scenes")
    await think(run_id, stage, "Validating plan structure…")

    state["outputs"][stage] = plan
    state["current_stage"] = Stage.RESEARCH.value

    await record_step(run_id, stage, "completed", plan)
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Plan ready ✓")
    return state
