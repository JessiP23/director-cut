"""Pydantic schemas for projects."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    template: Optional[str] = None


class ProjectOut(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str
    description: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    run_count: int = 0
