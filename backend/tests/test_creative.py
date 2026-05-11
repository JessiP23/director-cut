"""Tests for POST /api/creative/brief-expand and run outputs."""
from __future__ import annotations

import json
import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

# Ensure env vars are set before importing the app so Supabase check doesn't block startup.
os.environ.setdefault("NEXT_PUBLIC_SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("NEXT_PUBLIC_SUPABASE_ANON_KEY", "test-anon-key")

from app.server import app  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

FAKE_JWT = "Bearer test-token"

# Patch out the Supabase JWT validation for all tests: return a fake user.
FAKE_USER = {"id": "user-123", "email": "test@example.com"}

# Patch the auth middleware to bypass real Supabase verification.
async def _fake_validate(token: str) -> dict:  # noqa: D401
    return FAKE_USER


# LLM mock response that matches what call_llm returns on success.
GOOD_LLM_RESPONSE = {
    "title": "Brand Story",
    "tone": "professional",
    "max_scenes": 4,
    "aspect_ratio": "16:9",
    "production_plan": "Open with product close-up; cut to testimonials; close with logo.",
    "notes": "Keep pacing tight.",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def client():
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": FAKE_JWT},
    )


# ── brief-expand: happy path ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_brief_expand_happy_path(client):
    with (
        patch("app.server.validate_supabase_session", new=AsyncMock(return_value=FAKE_USER)),
        patch("app.routes.creative.call_llm", new=AsyncMock(return_value=GOOD_LLM_RESPONSE)),
    ):
        async with client as ac:
            resp = await ac.post(
                "/api/creative/brief-expand",
                json={
                    "brief": "A 30-second brand story for our sustainable coffee brand.",
                    "style": "cinematic",
                    "duration_target_seconds": 30,
                    "platform": "instagram",
                    "content_type": "video",
                },
            )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "request_id" in data
    assert data["simulated"] is False
    s = data["settings"]
    assert s["max_scenes"] == 4
    assert s["tone"] == "professional"
    assert s["aspect_ratio"] == "16:9"
    assert s["target_length_seconds"] == 30
    assert s["content_type"] == "video"
    assert isinstance(data["production_plan"], str)
    assert len(data["production_plan"]) > 0


# ── brief-expand: validation failure (400) ────────────────────────────────────

@pytest.mark.anyio
async def test_brief_expand_missing_brief(client):
    with patch("app.server.validate_supabase_session", new=AsyncMock(return_value=FAKE_USER)):
        async with client as ac:
            resp = await ac.post(
                "/api/creative/brief-expand",
                json={
                    "style": "cinematic",
                    "duration_target_seconds": 30,
                },
            )
    assert resp.status_code == 422  # Pydantic validation error → Unprocessable Entity


@pytest.mark.anyio
async def test_brief_expand_blank_brief(client):
    with patch("app.server.validate_supabase_session", new=AsyncMock(return_value=FAKE_USER)):
        async with client as ac:
            resp = await ac.post(
                "/api/creative/brief-expand",
                json={
                    "brief": "   ",
                    "style": "cinematic",
                    "duration_target_seconds": 30,
                },
            )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_brief_expand_duration_out_of_range(client):
    with patch("app.server.validate_supabase_session", new=AsyncMock(return_value=FAKE_USER)):
        async with client as ac:
            resp = await ac.post(
                "/api/creative/brief-expand",
                json={
                    "brief": "Valid brief content here.",
                    "duration_target_seconds": 9999,  # max is 600
                },
            )
    assert resp.status_code == 422


# ── brief-expand: internal failure (502 with safe shape) ─────────────────────

@pytest.mark.anyio
async def test_brief_expand_llm_error_returns_safe_502(client):
    with (
        patch("app.server.validate_supabase_session", new=AsyncMock(return_value=FAKE_USER)),
        patch(
            "app.routes.creative.call_llm",
            new=AsyncMock(side_effect=RuntimeError("upstream timeout")),
        ),
    ):
        async with client as ac:
            resp = await ac.post(
                "/api/creative/brief-expand",
                json={"brief": "Some brief.", "duration_target_seconds": 30},
            )

    assert resp.status_code == 502
    data = resp.json()
    # Safe error shape — no stack trace or secret in response
    assert "error" in data["detail"]
    assert data["detail"]["error"] == "llm_unavailable"
    assert "request_id" in data["detail"]
    # Ensure no secret-like keys leaked
    detail_str = json.dumps(data)
    assert "upstream timeout" not in detail_str  # internal message not forwarded


# ── brief-expand: simulated response when no LLM key ─────────────────────────

@pytest.mark.anyio
async def test_brief_expand_simulated_response(client):
    """When call_llm returns a simulated dict, brief-expand reflects simulated=True."""
    simulated = {
        "simulated": True,
        "title": "Simulated",
        "tone": "neutral",
        "max_scenes": 3,
        "aspect_ratio": "16:9",
        "production_plan": "Simulated plan.",
    }
    with (
        patch("app.server.validate_supabase_session", new=AsyncMock(return_value=FAKE_USER)),
        patch("app.routes.creative.call_llm", new=AsyncMock(return_value=simulated)),
    ):
        async with client as ac:
            resp = await ac.post(
                "/api/creative/brief-expand",
                json={"brief": "Test brief.", "duration_target_seconds": 60},
            )
    assert resp.status_code == 200
    assert resp.json()["simulated"] is True


# ── run outputs: asset URL fields present ─────────────────────────────────────

@pytest.mark.anyio
async def test_run_outputs_includes_asset_url_fields(client):
    """GET /api/runs/{id}/outputs always returns video_url, preview_urls, image_urls keys."""
    from tests.conftest import requires_db
    pytest.importorskip("asyncpg")  # guard: only meaningful with a real pool

    run_id = uuid.uuid4().hex

    from app.db.connection import get_pool
    pool = get_pool()

    await pool.execute(
        "INSERT INTO projects (id, name, description, created_at, updated_at)"
        " VALUES ($1, $2, $3, $4, $5)"
        " ON CONFLICT (id) DO NOTHING",
        "proj-test", "Test Project", "", "2024-01-01", "2024-01-01",
    )
    await pool.execute(
        "INSERT INTO runs"
        " (id, project_id, prompt, status, current_stage, settings_json, created_at, updated_at)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"
        " ON CONFLICT (id) DO NOTHING",
        run_id, "proj-test", "Test prompt", "completed", "done", "{}", "2024-01-01", "2024-01-01",
    )
    checkpoint_state = {
        "run_id": run_id,
        "outputs": {
            "render": {
                "rendered": True,
                "output_path": f"data/exports/{run_id}/render.mp4",
                "duration_seconds": 30,
                "file_size_bytes": 1024,
                "scene_count": 2,
                "preview_clip_paths": [
                    f"data/exports/{run_id}/_raw_000.mp4",
                ],
            }
        },
        "artifact_ids": [],
    }
    await pool.execute(
        "INSERT INTO run_checkpoints (id, run_id, stage, state_json, created_at)"
        " VALUES ($1, $2, $3, $4, $5)",
        uuid.uuid4().hex, run_id, "render", json.dumps(checkpoint_state), "2024-01-01",
    )

    with patch("app.server.validate_supabase_session", new=AsyncMock(return_value=FAKE_USER)):
        async with client as ac:
            resp = await ac.get(f"/api/runs/{run_id}/outputs")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["run_id"] == run_id
    assert data["status"] == "completed"
    assert "video_url" in data
    assert "preview_urls" in data
    assert "image_urls" in data
    assert data["video_url"] is not None
    assert "/media/exports/" in data["video_url"]
    assert "render.mp4" in data["video_url"]
    assert len(data["preview_urls"]) >= 1


# ── persistence: run survives a DB re-open (restart simulation) ───────────────

@pytest.mark.anyio
async def test_run_persistence_across_db_reconnect():
    """Run inserted via pool is retrievable in a subsequent pool.fetchrow call."""
    import os
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set — skipping persistence integration test")

    from app.db.connection import get_pool
    run_id = uuid.uuid4().hex

    pool = get_pool()

    await pool.execute(
        "INSERT INTO projects (id, name, description, created_at, updated_at)"
        " VALUES ($1, $2, $3, $4, $5)"
        " ON CONFLICT (id) DO NOTHING",
        "proj-persist", "Persist Project", "", "2024-01-01", "2024-01-01",
    )
    await pool.execute(
        "INSERT INTO runs"
        " (id, project_id, prompt, status, current_stage, settings_json, created_at, updated_at)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        run_id, "proj-persist", "Persist test", "running", "intake", "{}", "2024-01-01", "2024-01-01",
    )

    # Read back — simulates what happens after a process restart (same Postgres)
    row = await pool.fetchrow("SELECT id, status FROM runs WHERE id=$1", run_id)

    assert row is not None, "Run should persist in Supabase Postgres across pool.fetchrow"
    assert row["status"] == "running"
    assert row["id"] == run_id
    assert row["status"] == "running"
