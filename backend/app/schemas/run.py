"""Pydantic schemas for runs."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field
import uuid


class Stage(str, Enum):
    INTAKE = "intake"
    PLANNING = "planning"
    RESEARCH = "research"
    SCRIPT = "script"
    STORYBOARD = "storyboard"
    ASSETS = "assets"
    AUDIO = "audio"
    EDIT = "edit_assembly"
    QA = "qa"
    RENDER = "render"
    PACKAGE = "package"
    EXPORT = "export"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunCreate(BaseModel):
    project_id: str
    prompt: str
    settings: dict = Field(default_factory=dict)


class RunOut(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    project_id: str
    prompt: str
    status: RunStatus = RunStatus.PENDING
    current_stage: Stage = Stage.INTAKE
    next_stage: Optional[Stage] = None
    artifacts: List[str] = Field(default_factory=list)
    approvals_needed: List[str] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)
    retry_policy: dict = Field(default_factory=dict)
    logs_summary: str = ""
    export_ready: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_error: Optional[str] = None
