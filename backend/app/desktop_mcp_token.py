"""HS256-signed desktop bearer tokens for local MCP / automation."""

from __future__ import annotations

import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt


ALG = "HS256"
CLAIM_SUB = "sub"
TTL_DAYS = 30


async def ensure_signing_secret() -> str:
    """Return persisted HS256 secret, creating one on first use."""
    from app.db.repository import SettingsRepository

    repo = SettingsRepository()
    data = await repo.get_all()
    existing = (
        data.get("desktop_mcp_hs256_secret")
        or os.getenv("DIRECTOR_DESKTOP_MCP_SECRET", "").strip()
    )
    if existing and str(existing).strip():
        return str(existing).strip()
    secret = secrets.token_urlsafe(48)
    await repo.upsert({"desktop_mcp_hs256_secret": secret})
    return secret


def issue_desktop_token(secret: str) -> tuple[str, str]:
    """Return (jwt, iso8601 expires_at UTC)."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=TTL_DAYS)
    payload: dict[str, Any] = {
        "sub": f"desktop-mcp:{uuid.uuid4().hex[:12]}",
        "typ": "director-desktop-mcp",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, secret, algorithm=ALG)
    return token, exp.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def verify_desktop_token(token: str, secret: str) -> dict[str, Any] | None:
    """Validate JWT; return claims or None."""
    try:
        return jwt.decode(token, secret, algorithms=[ALG])
    except JWTError:
        return None
