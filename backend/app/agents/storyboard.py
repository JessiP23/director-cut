"""Storyboard agent – produce visual shot list / storyboard."""

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


async def storyboard_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.STORYBOARD.value
    await emit_progress(run_id, stage, "Creating storyboard…")

    await think(run_id, stage, "Reading approved script…")
    await think(run_id, stage, "Designing shot compositions and camera angles…")
    await think(run_id, stage, "Planning transitions between scenes…")

    max_scenes = _resolve_max_scenes(state)
    max_shots = max_scenes * 2

    storyboard = await call_llm(
        system=(
            "You are a storyboard artist. Given a script, output a shot list in JSON with "
            "shots[], each having: scene_id, description, camera_angle, transition, duration_seconds. "
            f"Return at most {max_shots} shots total."
        ),
        user=str(state["outputs"].get("script", {})),
        settings=state.get("settings", {}),
    )

    shots = storyboard.get("shots", [])
    if isinstance(shots, list) and len(shots) > max_shots:
        storyboard["shots"] = shots[:max_shots]
        await think(run_id, stage, f"Trimmed storyboard to {max_shots} shots")

    n_shots = len(storyboard.get("shots", []))
    await think(run_id, stage, f"Storyboard complete — {n_shots} shots designed")

    state["outputs"][stage] = storyboard
    state["current_stage"] = Stage.ASSETS.value
    state["needs_approval"] = stage

    await record_step(run_id, stage, "completed", storyboard)
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Storyboard ready – awaiting approval ⏳")
    return state
