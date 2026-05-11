"""Stateless creative-helper endpoints.

POST /api/creative/brief-expand
  - No MCP session required.
  - Accepts a creative brief and returns expanded production settings + plan.
  - mcp-director should call this instead of /mcp for brief expansion.
"""

from __future__ import annotations

import time
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.runtime.logger import get_logger
from app.services.llm import call_llm

log = get_logger("creative")
router = APIRouter()


# ── Request / Response schemas ───────────────────────────────────────────────

class BriefExpandRequest(BaseModel):
    brief: str = Field(..., min_length=1, max_length=8000)
    style: str = Field(default="cinematic")
    duration_target_seconds: int = Field(default=60, ge=5, le=600)
    platform: str = Field(default="general")
    content_type: Optional[Literal["video", "image"]] = Field(default="video")

    @field_validator("brief")
    @classmethod
    def brief_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("brief must not be blank")
        return v.strip()


class ProductionSettings(BaseModel):
    max_scenes: int
    target_length_seconds: int
    style: str
    platform: str
    content_type: str
    tone: str
    aspect_ratio: str


class BriefExpandResponse(BaseModel):
    request_id: str
    settings: ProductionSettings
    production_plan: str
    simulated: bool = False


# ── Endpoint ─────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a professional video production planner. Given a creative brief, respond with JSON only.

Required fields:
- title (string)
- tone (string: e.g. "professional", "playful", "dramatic")
- max_scenes (int, 1-8)
- aspect_ratio (string: "16:9", "9:16", or "1:1")
- production_plan (string, 1-3 sentence plain-English summary of the production approach)
- notes (string, optional guidance)

Match the style, platform, and duration_target_seconds from the user message.\
"""


@router.post(
    "/brief-expand",
    response_model=BriefExpandResponse,
    summary="Expand a creative brief into production settings and a plan",
    description=(
        "Stateless — no MCP session ID required. "
        "mcp-director should call this endpoint for brief expansion instead of /mcp."
    ),
)
async def brief_expand(body: BriefExpandRequest, request: Request) -> BriefExpandResponse:
    request_id = uuid.uuid4().hex
    t0 = time.perf_counter()

    log.info(
        "brief_expand_start",
        request_id=request_id,
        content_type=body.content_type,
        platform=body.platform,
        style=body.style,
        duration_target_seconds=body.duration_target_seconds,
        brief_len=len(body.brief),
    )

    user_msg = (
        f"Brief: {body.brief}\n"
        f"Style: {body.style}\n"
        f"Platform: {body.platform}\n"
        f"Duration target: {body.duration_target_seconds} seconds\n"
        f"Content type: {body.content_type or 'video'}"
    )

    # Reuse the same LLM gateway that all pipeline agents use.
    # Settings (API keys) are resolved from env / DB by call_llm itself.
    try:
        llm_out = await call_llm(
            system=_SYSTEM_PROMPT,
            user=user_msg,
            temperature=0.7,
            max_tokens=1024,
        )
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        log.error(
            "brief_expand_llm_error",
            request_id=request_id,
            error=str(exc)[:300],
            elapsed_ms=elapsed_ms,
        )
        raise HTTPException(
            status_code=502,
            detail={"error": "llm_unavailable", "request_id": request_id},
        ) from exc

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    simulated = bool(llm_out.get("simulated"))

    max_scenes = _clamp(llm_out.get("max_scenes", 4), 1, 8)
    tone = str(llm_out.get("tone") or "professional")
    aspect_ratio = str(llm_out.get("aspect_ratio") or "16:9")
    production_plan = str(
        llm_out.get("production_plan")
        or llm_out.get("notes")
        or llm_out.get("title")
        or "Production plan generated."
    )

    settings = ProductionSettings(
        max_scenes=max_scenes,
        target_length_seconds=body.duration_target_seconds,
        style=body.style,
        platform=body.platform,
        content_type=body.content_type or "video",
        tone=tone,
        aspect_ratio=aspect_ratio,
    )

    log.info(
        "brief_expand_ok",
        request_id=request_id,
        simulated=simulated,
        max_scenes=max_scenes,
        tone=tone,
        elapsed_ms=elapsed_ms,
        status_code=200,
    )

    return BriefExpandResponse(
        request_id=request_id,
        settings=settings,
        production_plan=production_plan,
        simulated=simulated,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clamp(val: object, lo: int, hi: int) -> int:
    try:
        return max(lo, min(int(val), hi))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return lo
