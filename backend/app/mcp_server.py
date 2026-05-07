"""FastMCP Streamable HTTP mount, auth middleware, and event-bus notification bridge."""

from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar
from typing import Any

from fastapi import HTTPException
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.middleware import Middleware, MiddlewareContext

from app.desktop_mcp_token import ensure_signing_secret, verify_desktop_token
from app.mcp_tools import pipeline as mcp_pipeline
from app.mcp_tools import services as mcp_services
from app.runtime.event_bus import EventBus

MCP_UNAUTHORIZED = -32001

_mcp_starlette: Any | None = None
_mcp_fastmcp: FastMCP | None = None
_emit_bridge_installed = False

# Active MCP notify queues keyed by Streamable HTTP session id (monitored hooks / tests).
_mcp_notify_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}

# Authenticated MCP principal (validated in middleware).
mcp_principal: ContextVar[dict[str, Any] | None] = ContextVar(
    "mcp_principal",
    default=None,
)


def director_mcp_session_count() -> int:
    return len(_mcp_notify_queues)


def director_mcp_tool_count() -> int:
    """Tool count for settings summary (see mcp_pipeline + mcp_services registration)."""
    from app.graph.engine import STAGE_ORDER

    return 12 + len(STAGE_ORDER)  # run/project/output/service + one run_single + all stage passthroughs


AUTH_WHITELIST = frozenset(
    (
        "ping",
        "notifications/initialized",
    )
)


def _extract_bearer() -> str | None:
    try:
        req = get_http_request()
    except Exception:
        return None
    hdr = req.headers.get("authorization") or req.headers.get("Authorization") or ""
    if hdr.lower().startswith("bearer "):
        tok = hdr[7:].strip()
        return tok or None
    return None


def _session_id_from_request() -> str | None:
    try:
        req = get_http_request()
    except Exception:
        return None
    for key in ("mcp-session-id", "Mcp-Session-Id", "MCP-Session-Id"):
        val = req.headers.get(key)
        if val and val.strip():
            return val.strip()
    return None


async def authenticate_mcp_bearer(token: str) -> dict[str, Any]:
    """Validate Supabase session or desktop HS256 token; return a JSON-serializable principal."""
    secret = await ensure_signing_secret()
    claims = verify_desktop_token(token, secret)
    if claims:
        sub = str(claims.get("sub") or "desktop-mcp")
        return {"kind": "desktop", "id": sub, "claims": claims}

    # Lazy import to avoid circular bootstrap with FastAPI server module.
    from app.server import validate_supabase_session

    try:
        sb_user = await validate_supabase_session(token)
    except HTTPException:
        raise McpError(ErrorData(code=MCP_UNAUTHORIZED, message="Unauthorized")) from None

    uid = sb_user.get("id") if isinstance(sb_user, dict) else None
    if not uid:
        uid = sb_user.get("user", {}).get("id") if isinstance(sb_user.get("user"), dict) else ""
    uid = uid or ""
    return {"kind": "supabase", "id": str(uid), "user": sb_user}


async def ensure_mcp_queue_for_session() -> asyncio.Queue | None:
    sid = _session_id_from_request()
    if not sid:
        return None
    if sid not in _mcp_notify_queues:
        _mcp_notify_queues[sid] = asyncio.Queue(maxsize=512)
    return _mcp_notify_queues[sid]


async def dequeue_mcp_test_notification(session_id: str, timeout: float = 1.0) -> dict[str, Any] | None:
    """Test helper: retrieve one bridged MCP notification envelope."""
    q = _mcp_notify_queues.get(session_id)
    if not q:
        return None
    return await asyncio.wait_for(q.get(), timeout=timeout)


def unregister_mcp_session(session_id: str) -> None:
    _mcp_notify_queues.pop(session_id, None)


def _enqueue_mcp_notification(note: dict[str, Any]) -> None:
    for _, q in list(_mcp_notify_queues.items()):
        try:
            q.put_nowait(note)
        except asyncio.QueueFull:
            continue


def _install_emit_bridge_once() -> None:
    global _emit_bridge_installed
    if _emit_bridge_installed:
        return

    original = EventBus.emit

    async def emit_with_mcp(self: EventBus, run_id: str, event_type: str, data: dict) -> None:
        await original(self, run_id, event_type, data)
        note = {
            "jsonrpc": "2.0",
            "method": "notifications/message",
            "params": {
                "level": "info",
                "data": {
                    "run_id": run_id,
                    "type": event_type,
                    "payload": dict(data),
                },
            },
        }
        _enqueue_mcp_notification(note)

    EventBus.emit = emit_with_mcp  # type: ignore[assignment]

    _emit_bridge_installed = True


class DirectorMCPAuthMiddleware(Middleware):
    """Validate Authorization before MCP operations return sensitive data."""

    async def on_request(
        self,
        context: MiddlewareContext[Any],
        call_next,
    ):
        method = getattr(context, "method", None)
        if method in AUTH_WHITELIST:
            return await call_next(context)

        token = _extract_bearer()
        if not token:
            raise McpError(ErrorData(code=MCP_UNAUTHORIZED, message="Unauthorized"))

        user = await authenticate_mcp_bearer(token)
        await ensure_mcp_queue_for_session()
        tok = mcp_principal.set(user)
        try:
            return await call_next(context)
        finally:
            mcp_principal.reset(tok)


def get_mcp_principal() -> dict[str, Any]:
    p = mcp_principal.get()
    if not p:
        raise RuntimeError("MCP principal missing — middleware not applied")
    return p


def _build_fastmcp() -> FastMCP:
    mcp = FastMCP("director-cut", version="1.0.0")
    mcp.add_middleware(DirectorMCPAuthMiddleware())

    mcp_pipeline.register_pipeline_tools(mcp)
    mcp_services.register_service_tools(mcp)

    @mcp.resource("director://runs/{run_id}/state")
    async def director_run_checkpoint_resource(run_id: str) -> str:
        """Latest SQLite checkpoint JSON for a run (outputs + stage state)."""
        from app.db.repository import CheckpointRepository

        repo = CheckpointRepository()
        state = await repo.latest(run_id)
        if not state:
            return json.dumps({"error": "no_checkpoint", "run_id": run_id})
        return json.dumps(state, default=str)

    @mcp.prompt
    async def director_prompts_quick_run(
        project_id: str,
        prompt: str,
        settings: dict | None = None,
    ) -> str:
        """Structured kickoff content for the director.run.create tool.

        Fills project_id, prompt, and optional settings for a one-shot production.
        """
        payload = {
            "project_id": project_id,
            "prompt": prompt,
            "settings": dict(settings or {}),
        }
        return (
            "Use director.run.create with the following JSON arguments:\n"
            f"```json\n{json.dumps(payload, indent=2)}\n```"
        )

    return mcp


def get_or_build_mcp_starlette():
    global _mcp_starlette, _mcp_fastmcp
    if _mcp_starlette is None:
        _install_emit_bridge_once()
        _mcp_fastmcp = _build_fastmcp()
        _mcp_starlette = _mcp_fastmcp.http_app(
            path="/",
            transport="streamable-http",
            json_response=True,
        )
    return _mcp_starlette


def mount_mcp(app) -> None:
    """Mount Streamable HTTP MCP under /mcp (call after all API routes)."""
    mcp_app = get_or_build_mcp_starlette()
    app.mount("/mcp", mcp_app)
