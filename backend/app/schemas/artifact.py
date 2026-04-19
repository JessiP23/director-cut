"""Pydantic schemas for artifacts."""

from datetime import datetime
from pydantic import BaseModel, Field
import uuid


class ArtifactOut(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    run_id: str
    stage: str
    kind: str  # script, storyboard, image, audio, video, subtitle, etc.
    path: str
    version: int = 1
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
