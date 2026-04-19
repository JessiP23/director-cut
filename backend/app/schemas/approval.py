"""Pydantic schemas for approvals."""

from enum import Enum
from pydantic import BaseModel


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REVISE = "revise"


class ApprovalAction(BaseModel):
    stage: str
    decision: ApprovalDecision
    notes: str = ""
