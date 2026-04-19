"""Typed state that flows through the LangGraph pipeline."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.schemas.run import Stage, RunStatus


class PipelineState(BaseModel):
    """The mutable state bag that every graph node reads and writes."""

    run_id: str
    project_id: str
    prompt: str
    status: RunStatus = RunStatus.RUNNING
    current_stage: Stage = Stage.INTAKE

    # Accumulated outputs keyed by stage name
    outputs: Dict[str, Any] = Field(default_factory=dict)

    # Artifact IDs produced so far
    artifact_ids: List[str] = Field(default_factory=list)

    # Approval decisions received
    approvals: Dict[str, str] = Field(default_factory=dict)

    # Error log for retries
    errors: List[dict] = Field(default_factory=list)

    # Settings snapshot for this run
    settings: dict = Field(default_factory=dict)

    # Flags
    cancelled: bool = False
    needs_approval: Optional[str] = None  # stage name awaiting approval
