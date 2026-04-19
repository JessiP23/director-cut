"""Approval gate routes."""

from fastapi import APIRouter
from app.schemas.approval import ApprovalAction

router = APIRouter()


@router.post("/{run_id}")
async def approve(run_id: str, body: ApprovalAction):
    from app.graph.engine import submit_approval
    await submit_approval(run_id, body.stage, body.decision, body.notes or "")
    return {"ok": True}
