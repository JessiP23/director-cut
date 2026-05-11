"""Run management routes."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request

from app.db.connection import get_pool
from app.schemas.run import RunCreate, RunOut, RunStatus, Stage

router = APIRouter()


async def _latest_error_message(pool, run_id: str) -> str | None:
    row = await pool.fetchrow(
        "SELECT message FROM errors WHERE run_id=$1 ORDER BY created_at DESC LIMIT 1",
        run_id,
    )
    if not row or not row["message"]:
        return None
    s = str(row["message"]).strip()
    return s or None


@router.post("/", response_model=RunOut)
async def create_run(body: RunCreate):
    pool = get_pool()
    run_id = uuid.uuid4().hex
    now = datetime.utcnow().isoformat()
    await pool.execute(
        "INSERT INTO runs"
        " (id, project_id, prompt, status, current_stage, settings_json, created_at, updated_at)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        run_id, body.project_id, body.prompt, "running", "intake",
        json.dumps(body.settings), now, now,
    )

    try:
        from app.graph.engine import start_run_async
        asyncio.create_task(start_run_async(run_id, body))
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"⚠️ Pipeline engine failed to start: {e}")

    return RunOut(
        id=run_id, project_id=body.project_id, prompt=body.prompt,
        status=RunStatus.RUNNING, current_stage=Stage.INTAKE, created_at=now, updated_at=now,
    )


@router.get("/", response_model=list[RunOut])
async def list_runs():
    pool = get_pool()
    rows = await pool.fetch("SELECT * FROM runs ORDER BY created_at DESC")
    return [
        RunOut(
            id=r["id"], project_id=r["project_id"], prompt=r["prompt"],
            status=r["status"], current_stage=r["current_stage"],
            created_at=r["created_at"], updated_at=r["updated_at"],
        )
        for r in rows
    ]


@router.get("/{run_id}/errors")
async def get_run_errors(run_id: str):
    """Latest pipeline errors for a run (populated when status is failed)."""
    pool = get_pool()
    row = await pool.fetchrow("SELECT id FROM runs WHERE id=$1", run_id)
    if not row:
        raise HTTPException(404, "Run not found")
    rows = await pool.fetch(
        "SELECT message, stage, created_at FROM errors WHERE run_id=$1 "
        "ORDER BY created_at DESC LIMIT 10",
        run_id,
    )
    return {
        "run_id": run_id,
        "errors": [
            {
                "message": r["message"],
                "stage": r["stage"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


@router.post("/{run_id}/cancel")
async def cancel(run_id: str):
    from app.graph.engine import cancel_run
    await cancel_run(run_id)
    return {"ok": True}


@router.post("/{run_id}/resume")
async def resume(run_id: str):
    return {"ok": True, "message": "Resume not yet implemented"}


@router.get("/{run_id}/outputs")
async def get_run_outputs(run_id: str, request: Request):
    """Return checkpoint state, stage outputs, and stable asset URLs for a run.

    Asset URL fields in the response:
    - video_url: HTTPS/HTTP URL to the final rendered video (None if not yet rendered).
    - preview_urls: list of preview clip URLs (may be empty).
    - image_urls: list of image asset URLs (may be empty).

    All URLs are absolute using the server's own origin so mcp-director can
    reference them without knowing the deployment hostname.
    """
    pool = get_pool()
    from app.db.repository import ArtifactRepository

    row = await pool.fetchrow(
        "SELECT state_json FROM checkpoints WHERE run_id=$1 ORDER BY created_at DESC LIMIT 1",
        run_id,
    )
    run_row = await pool.fetchrow(
        "SELECT status, current_stage FROM runs WHERE id=$1", run_id
    )

    if not run_row:
        raise HTTPException(404, "Run not found")

    outputs: dict = {}
    artifact_ids: list = []
    if row:
        state = json.loads(row["state_json"])
        outputs = state.get("outputs", {})
        artifact_ids = state.get("artifact_ids", [])

    repo = ArtifactRepository()
    artifacts = await repo.list_for_run(run_id)

    base_url = _base_url(request)

    video_url: str | None = None
    preview_urls: list[str] = []
    image_urls: list[str] = []

    for art in artifacts:
        url = _artifact_url(base_url, art.path)
        if art.kind == "video":
            if "render.mp4" in art.path or video_url is None:
                video_url = url
        elif art.kind in ("image", "frame"):
            image_urls.append(url)

    render_out = outputs.get("render", {})
    if not video_url and render_out.get("output_path"):
        video_url = _artifact_url(base_url, render_out["output_path"])
    for p in render_out.get("preview_clip_paths", []):
        url = _artifact_url(base_url, p)
        if url not in preview_urls:
            preview_urls.append(url)

    return {
        "run_id": run_id,
        "status": run_row["status"],
        "current_stage": run_row["current_stage"],
        "outputs": outputs,
        "artifact_ids": artifact_ids,
        "video_url": video_url,
        "preview_urls": preview_urls,
        "image_urls": image_urls,
    }


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: str):
    """Run row plus latest pipeline error message when the run has failed."""
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM runs WHERE id=$1", run_id)
    if not row:
        raise HTTPException(404, "Run not found")
    last_error = await _latest_error_message(pool, run_id)
    return RunOut(
        id=row["id"], project_id=row["project_id"], prompt=row["prompt"],
        status=row["status"], current_stage=row["current_stage"],
        created_at=row["created_at"], updated_at=row["updated_at"],
        last_error=last_error,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _base_url(request: Request) -> str:
    """Return scheme://host (no trailing slash), honouring x-forwarded-proto from Fly/proxy."""
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    return f"{scheme}://{host}"


def _artifact_url(base: str, path: str) -> str:
    """Convert a relative data/exports/… path to an absolute /media/exports/… URL."""
    clean = path.replace("\\", "/")
    for prefix in ("data/exports/", "backend/data/exports/"):
        if clean.startswith(prefix):
            clean = clean[len(prefix):]
            break
    return f"{base}/media/exports/{clean}"
