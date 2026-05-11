"""PostgreSQL connection pool (asyncpg) + schema bootstrap.

DATABASE_URL must be set as a Fly secret (or in .env):
  fly secrets set DATABASE_URL="postgresql://postgres:<pw>@db.<project>.supabase.co:5432/postgres"
"""

from __future__ import annotations

import os
from typing import Optional

import asyncpg

_pool: Optional[asyncpg.Pool] = None

# Each CREATE TABLE statement is executed separately (asyncpg doesn't support scripts).
_SCHEMA: list[str] = [
    """CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        prompt TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        current_stage TEXT NOT NULL DEFAULT 'intake',
        settings_json TEXT DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS run_steps (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id),
        stage TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        input_json TEXT DEFAULT '{}',
        output_json TEXT DEFAULT '{}',
        error TEXT,
        started_at TEXT,
        finished_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id),
        stage TEXT NOT NULL,
        kind TEXT NOT NULL,
        path TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        metadata_json TEXT DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS approvals (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id),
        stage TEXT NOT NULL,
        decision TEXT NOT NULL,
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS tool_calls (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id),
        stage TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        input_json TEXT DEFAULT '{}',
        output_json TEXT DEFAULT '{}',
        duration_ms INTEGER,
        error TEXT,
        created_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS errors (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id),
        stage TEXT,
        message TEXT NOT NULL,
        traceback TEXT,
        recoverable INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS checkpoints (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id),
        stage TEXT NOT NULL,
        state_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS exports (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id),
        preset TEXT NOT NULL,
        path TEXT NOT NULL,
        metadata_json TEXT DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS costs (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id),
        stage TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT,
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        cost_usd DOUBLE PRECISION DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS sources (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id),
        url TEXT,
        kind TEXT NOT NULL,
        metadata_json TEXT DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS prompts (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id),
        stage TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS assets (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id),
        kind TEXT NOT NULL,
        source_url TEXT,
        local_path TEXT,
        metadata_json TEXT DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT ''
    )""",
]


def get_pool() -> asyncpg.Pool:
    """Return the active connection pool. Raises if init_db() was not called."""
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call init_db() at startup")
    return _pool


async def init_db() -> asyncpg.Pool:
    """Create the asyncpg pool and ensure all tables exist. Call once at startup."""
    global _pool
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. "
            "Run: fly secrets set DATABASE_URL='postgresql://...' "
            "or add it to your .env file."
        )
    _pool = await asyncpg.create_pool(url, min_size=1, max_size=10)
    async with _pool.acquire() as conn:
        for stmt in _SCHEMA:
            await conn.execute(stmt)
    return _pool


async def close_pool() -> None:
    """Gracefully close the connection pool at shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
