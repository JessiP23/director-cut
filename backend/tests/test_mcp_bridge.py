"""MCP bridge tests (Starlette TestClient + pytest)."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from pathlib import Path

import pytest
from starlette.testclient import TestClient

_tmp = tempfile.NamedTemporaryFile(prefix="mcp_test_", suffix=".sqlite", delete=False)
_tmp.close()
os.environ["DIRECTOR_DB"] = _tmp.name

os.environ.setdefault("NEXT_PUBLIC_SUPABASE_URL", "http://supabase.test")
os.environ.setdefault("NEXT_PUBLIC_SUPABASE_ANON_KEY", "anon-test-key")

from app import mcp_server  # noqa: E402
from app.runtime.event_bus import event_bus  # noqa: E402
from app.server import app  # noqa: E402

HEADERS_BASE = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


@pytest.fixture
def api_client(monkeypatch):
    async def _mock_validate(token: str):
        if token != "mock-token":
            from fastapi import HTTPException

            raise HTTPException(status_code=401, detail="bad token")
        return {"id": "test-user", "email": "t@example.com"}

    monkeypatch.setattr("app.server.validate_supabase_session", _mock_validate)

    async def _noop_engine_start(_run_id, _body):
        return None

    monkeypatch.setattr("app.graph.engine.start_run_async", _noop_engine_start)

    captured: list = []

    def _fake_create_task(coro):
        captured.append(coro)

        class _T:
            def add_done_callback(self, *_a, **_k):
                return None

        return _T()

    monkeypatch.setattr(asyncio, "create_task", _fake_create_task)

    with TestClient(app) as c:
        yield c


def test_mcp_health_ok(api_client):
    r = api_client.get("/mcp/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_initialize_without_auth_is_rejected(api_client):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "pytest", "version": "1"},
        },
    }
    r = api_client.post("/mcp", json=body, headers=HEADERS_BASE)
    if r.status_code == 401:
        return
    data = r.json()
    assert "error" in data
    assert data["error"]["code"] == mcp_server.MCP_UNAUTHORIZED


def _open_session(api_client: TestClient, token: str) -> str:
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "pytest", "version": "1"},
        },
    }
    h = {**HEADERS_BASE, "Authorization": f"Bearer {token}"}
    r = api_client.post("/mcp", json=body, headers=h)
    assert r.status_code in (200, 202), r.text
    sid = r.headers.get("mcp-session-id")
    assert sid
    note = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    h2 = {**h, "Mcp-Session-Id": sid}
    r2 = api_client.post("/mcp", content=note, headers=h2)
    assert r2.status_code in (200, 202), r2.text
    return sid


def test_tools_list_includes_run_create(api_client):
    sid = _open_session(api_client, "mock-token")
    h = {**HEADERS_BASE, "Authorization": "Bearer mock-token", "Mcp-Session-Id": sid}
    body = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    r = api_client.post("/mcp", json=body, headers=h)
    assert r.status_code in (200, 202), r.text
    payload = r.json()
    assert "result" in payload
    names = [t["name"] for t in payload["result"].get("tools", [])]
    assert "director.run.create" in names


def test_run_list_empty_ok(api_client):
    sid = _open_session(api_client, "mock-token")
    h = {**HEADERS_BASE, "Authorization": "Bearer mock-token", "Mcp-Session-Id": sid}
    body = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "director.run.list", "arguments": {}}}
    r = api_client.post("/mcp", json=body, headers=h)
    assert r.status_code in (200, 202), r.text
    assert "result" in r.json()


def test_run_create_inserts_run(api_client):
    from app.db.connection import get_db

    async def seed():
        db = await get_db()
        pid = uuid.uuid4().hex
        await db.execute(
            "INSERT INTO projects (id,name,description,created_at,updated_at) VALUES (?,?,?,datetime('now'),datetime('now'))",
            (pid, "MCP Test", ""),
        )
        await db.commit()
        await db.close()
        return pid

    pid = asyncio.run(seed())

    sid = _open_session(api_client, "mock-token")
    h = {**HEADERS_BASE, "Authorization": "Bearer mock-token", "Mcp-Session-Id": sid}
    args = {"project_id": pid, "prompt": "hello mcp", "settings": {}}
    body = {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "director.run.create", "arguments": args}}
    r = api_client.post("/mcp", json=body, headers=h)
    assert r.status_code in (200, 202), r.text
    data = r.json()
    assert "result" in data
    text = data["result"]["content"][0]["text"]
    inner = json.loads(text)
    assert inner.get("run_id")


def test_run_single_missing_run_structured_error(api_client):
    sid = _open_session(api_client, "mock-token")
    h = {**HEADERS_BASE, "Authorization": "Bearer mock-token", "Mcp-Session-Id": sid}
    args = {"run_id": "nosuchrun", "stage": "intake", "override_state": {}}
    body = {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "director.stage.run_single", "arguments": args}}
    r = api_client.post("/mcp", json=body, headers=h)
    assert r.status_code in (200, 202), r.text
    data = r.json()
    assert "result" in data
    text = data["result"]["content"][0]["text"]
    inner = json.loads(text)
    assert inner.get("error") == "not_found"


def test_event_bus_bridge_queue(api_client):
    sid = _open_session(api_client, "mock-token")
    q = asyncio.Queue()
    mcp_server._mcp_notify_queues[sid] = q

    async def go():
        await event_bus.emit("run-x", "custom_evt", {"hello": "world"})

    asyncio.run(go())
    note = asyncio.run(_wait(q))

    assert note["method"] == "notifications/message"
    assert note["params"]["data"]["type"] == "custom_evt"


async def _wait(q: asyncio.Queue):
    return await asyncio.wait_for(q.get(), timeout=2.0)


def teardown_module():
    try:
        Path(os.environ["DIRECTOR_DB"]).unlink(missing_ok=True)
    except OSError:
        pass
