"""Script agent – generate narration / dialogue script."""

from app.agents.base import checkpoint, record_step, emit_progress, think
from app.schemas.run import Stage
from app.services.llm import call_llm


async def script_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.SCRIPT.value
    await emit_progress(run_id, stage, "Writing script…")

    await think(run_id, stage, "Combining plan + research into a narrative arc…")
    await think(run_id, stage, "Drafting scene-by-scene narration with timings…")

    script = await call_llm(
        system="You are a scriptwriter. Given plan and research, write a timed narration script in JSON with scenes[], each having: id, text, duration_seconds, visual_direction.",
        user=str({k: state["outputs"].get(k) for k in ("planning", "research")}),
        settings=state.get("settings", {}),
    )

    n_scenes = len(script.get("scenes", script.get("script_lines", [])))
    await think(run_id, stage, f"Script drafted — {n_scenes} scenes written")
    await think(run_id, stage, "Script ready for human review.")

    state["outputs"][stage] = script
    state["current_stage"] = Stage.STORYBOARD.value
    state["needs_approval"] = stage

    await record_step(run_id, stage, "completed", script)
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Script ready – awaiting approval ⏳")
    return state
