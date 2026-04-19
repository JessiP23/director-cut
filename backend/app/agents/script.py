"""Script agent – generate narration / dialogue script."""

from app.agents.base import checkpoint, record_step, emit_progress
from app.schemas.run import Stage
from app.services.llm import call_llm


async def script_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.SCRIPT.value
    await emit_progress(run_id, stage, "Writing script…")

    script = await call_llm(
        system="You are a scriptwriter. Given plan and research, write a timed narration script in JSON with scenes, lines, and durations.",
        user=str({k: state["outputs"].get(k) for k in ("planning", "research")}),
        settings=state.get("settings", {}),
    )

    state["outputs"][stage] = script
    state["current_stage"] = Stage.STORYBOARD.value
    state["needs_approval"] = stage  # gate

    await record_step(run_id, stage, "completed", script)
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Script ready – awaiting approval.")
    return state
