"""Export agent – write final files, record in DB."""

import uuid
from app.agents.base import checkpoint, record_step, emit_progress, think
from app.db.repository import ArtifactRepository
from app.schemas.run import Stage


async def export_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.EXPORT.value
    await emit_progress(run_id, stage, "Exporting…")

    await think(run_id, stage, "Writing final output files…")

    repo = ArtifactRepository()
    render_path = state["outputs"].get("render", {}).get("output_path", "")
    if render_path:
        aid = await repo.save(run_id, stage, "video", render_path)
        state["artifact_ids"].append(aid)
        await think(run_id, stage, f"Artifact saved: {render_path}")

    await think(run_id, stage, "Recording metadata and closing project…")

    state["outputs"][stage] = {"exported": True, "artifact_count": len(state["artifact_ids"])}
    state["current_stage"] = "done"
    state["status"] = "completed"

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Export complete 🎬")
    return state
