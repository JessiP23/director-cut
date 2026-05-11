"""Shared pytest fixtures for director-cut backend tests.

DB strategy:
- If DATABASE_URL is set → use a real asyncpg pool (integration mode).
- Otherwise            → mock the pool so unit tests pass without Postgres.

Tests that need a real DB should be decorated with @requires_db.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

HAS_DB = bool(os.getenv("DATABASE_URL", ""))

# Convenience marker for callers.
requires_db = pytest.mark.skipif(not HAS_DB, reason="DATABASE_URL not set")


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True, scope="session")
async def db_lifecycle():
    """Ensure the asyncpg pool is available (or mocked) for the entire test session."""
    if HAS_DB:
        from app.db.connection import init_db, close_pool
        await init_db()
        yield
        await close_pool()
    else:
        # Provide a stateful in-memory mock so that tests that don't exercise the
        # DB can still boot the FastAPI app without a real Postgres connection.
        mock_pool = AsyncMock()
        mock_pool.execute = AsyncMock(return_value="OK")
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_pool.executemany = AsyncMock(return_value=None)

        import app.db.connection as _conn
        with (
            patch.object(_conn, "_pool", mock_pool),
            patch.object(_conn, "get_pool", return_value=mock_pool),
            patch.object(_conn, "init_db", new=AsyncMock(return_value=mock_pool)),
            patch.object(_conn, "close_pool", new=AsyncMock()),
        ):
            yield
