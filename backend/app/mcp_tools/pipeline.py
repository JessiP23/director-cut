"""Director pipeline MCP tools: runs, stages, projects, outputs."""

from __future__ import annotations

import json
import uuid

from fastmcp import Context, FastMCP

from app.schemas.project import ProjectCreate
from app.schemas.run import RunCreate, Stage
from app.graph.engine import NODE_MAP, STAGE_ORDER, cancel_run as engine_cancel_run, pipeline_task_active
from app.db.repository import CheckpointRepository, ProjectRepository
from app.db.connection import get_db


def _stage_values() -> frozenset[str]:
    return frozenset(s.value for s in STAGE_ORDER)


async def _run_row(run_id: str) -> dict | None:
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM runs WHERE id=?", (run_id,))
        row = await cur.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def _run_summaries(limit: int, project_id: str | None) -> list[dict]:
    db = await get_db()
    try:
        if project_id:
            cur = await db.execute(
                "SELECT id, project_id, prompt, status, current_stage, created_at, updated_at FROM runs "
                "WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            )
        else:
            cur = await db.execute(
                "SELECT id, project_id, prompt, status, current_stage, created_at, updated_at FROM runs "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


def register_pipeline_tools(mcp: FastMCP) -> None:
    """Attach director.run.* director.project.* director.stage.* and director.output.* tools."""

    stages_ok = _stage_values()

    @mcp.tool(name="director.run.create")
    async def director_run_create(
        ctx: Context,
        project_id: str,
        prompt: str,
        settings: dict | None = None,
    ) -> dict:
        """Start a production run by invoking the existing FastAPI run creation flow."""
        settings = dict(settings or {})
        await ctx.report_progress(10, 100, message="Enqueueing pipeline")
        from app.routes.runs import create_run
        from app.mcp_server import get_mcp_principal

        uid = str(get_mcp_principal().get("id") or "")

        payload = RunCreate(project_id=project_id, prompt=prompt, settings=settings)
        out = await create_run(payload)
        await ctx.report_progress(100, 100, message="Run queued")
        return {
            "run_id": out.id,
            "status": getattr(out.status, "value", str(out.status)),
            "created_at": str(out.created_at),
            "user_id": uid,
        }

    @mcp.tool(name="director.run.cancel")
    async def director_run_cancel(ctx: Context, run_id: str) -> dict:
        """Cancel an in-flight pipeline task and mark the run cancelled in SQLite."""
        await ctx.report_progress(20, 100, message="Cancelling")
        had_task = pipeline_task_active(run_id)
        await engine_cancel_run(run_id)
        await ctx.report_progress(100, 100, message="Done")
        return {"run_id": run_id, "cancelled": had_task}

    @mcp.tool(name="director.run.status")
    async def director_run_status(run_id: str) -> dict:
        """Return the full runs table row for a single run_id."""
        row = await _run_row(run_id)
        if not row:
            return {"error": "not_found", "run_id": run_id}
        return {"run": row}

    @mcp.tool(name="director.run.outputs")
    async def director_run_outputs(run_id: str) -> dict:
        """Return the latest checkpoint outputs map for a run."""
        checkpoint = CheckpointRepository()
        state = await checkpoint.latest(run_id)
        if not state:
            return {"error": "no_checkpoint", "run_id": run_id}
        return {"run_id": run_id, "outputs": state.get("outputs", {})}

    @mcp.tool(name="director.run.list")
    async def director_run_list(
        ctx: Context,
        project_id: str | None = None,
        limit: int = 20,
    ) -> dict:
        """List recent productions (optionally filtered by project_id)."""
        await ctx.report_progress(10, 100, message="Listing runs")
        lim = max(1, min(int(limit), 200))
        pid = project_id.strip() if project_id and project_id.strip() else None
        rows = await _run_summaries(lim, pid)
        await ctx.report_progress(100, 100, message=f"{len(rows)} runs")
        return {"runs": rows}

    @mcp.tool(name="director.stage.run_single")
    async def director_stage_run_single(
        ctx: Context,
        run_id: str,
        stage: str,
        override_state: dict | None = None,
    ) -> dict:
        """POWER TOOL — re-run exactly one pipeline stage from the latest checkpoint (no active pipeline)."""
        overrides = dict(override_state or {})
        await ctx.report_progress(5, 100, message="Checking eligibility")
        if pipeline_task_active(run_id):
            return {"error": "pipeline_busy", "message": "Run has an active pipeline task"}

        row = await _run_row(run_id)
        if not row:
            return {"error": "not_found", "run_id": run_id}

        status = str(row.get("status") or "").lower()
        if status == "running":
            return {
                "error": "invalid_status",
                "message": "Run is marked running — wait for pause/failure or cancel first",
                "status": status,
            }

        st = stage.strip().lower()
        if st not in stages_ok:
            return {"error": "unknown_stage", "stage": stage, "allowed": sorted(stages_ok)}

        enum_stage = Stage(st)
        repo = CheckpointRepository()
        prev = await repo.latest(run_id)
        if not prev:
            return {"error": "no_checkpoint", "run_id": run_id}

        state = dict(prev)
        state.update(overrides)
        state.setdefault("outputs", {})

        outputs_before_keys = set((state.get("outputs") or {}).keys())

        await ctx.report_progress(30, 100, message=f"Running {st}")
        node_fn = NODE_MAP[enum_stage]
        new_state = await node_fn(state)
        await repo.save(run_id, enum_stage.value, new_state)

        outs = new_state.get("outputs") or {}
        delta_keys = set(outs.keys()) - outputs_before_keys
        outputs_delta = {k: outs[k] for k in delta_keys}

        await ctx.report_progress(100, 100, message="Checkpoint saved")
        return {"stage": enum_stage.value, "status": "ok", "outputs_delta": outputs_delta}

    # Per-stage passthrough wrappers (delegate to run_single).
    for sv in sorted(stages_ok):

        async def _wrapped(
            ctx: Context,
            run_id: str,
            override_state: dict | None = None,
            *,
            _fixed: str = sv,
        ) -> dict:
            return await director_stage_run_single(ctx, run_id, _fixed, override_state)

        _wrapped.__name__ = f"director_stage_{sv.replace('.', '_')}"
        _wrapped.__doc__ = (
            f"Passthrough that runs only the `{sv}` stage once using the latest checkpoint. "
            "Same rules as director.stage.run_single (no active pipeline driver; not for `running` rows)."
        )

        dec = mcp.tool(name=f"director.stage.{sv}")
        dec(_wrapped)

    @mcp.tool(name="director.project.create")
    async def director_project_create(ctx: Context, name: str, description: str = "") -> dict:
        """Create a SQLite-backed project."""
        await ctx.report_progress(20, 100, message="Creating project")
        repo = ProjectRepository()
        proj = await repo.create(ProjectCreate(name=name, description=description or ""))
        await ctx.report_progress(100, 100, message="Project created")
        return {"project_id": proj.id, "name": proj.name}

    @mcp.tool(name="director.project.list")
    async def director_project_list(ctx: Context) -> dict:
        """Return all projects."""
        await ctx.report_progress(20, 100, message="Loading projects")
        repo = ProjectRepository()
        rows = await repo.list_all()
        await ctx.report_progress(100, 100, message=f"{len(rows)} projects")
        return {"projects": [p.model_dump(mode="json") for p in rows]}

    @mcp.tool(name="director.output.latest_state")
    async def director_output_latest_state(run_id: str) -> dict:
        """Return the newest checkpoint JSON (full state blob) including outputs."""
        repo = CheckpointRepository()
        latest = await repo.latest(run_id)
        if not latest:
            return {"error": "no_checkpoint", "run_id": run_id}
        safe = json.loads(json.dumps(latest, default=str))
        return {"run_id": run_id, "state": safe}

