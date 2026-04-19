"""Approval gate routes."""

import uuid
from fastapi import APIRouter
from app.schemas.approval import ApprovalAction

router = APIRouter()


@router.post("/{run_id}")
async def approve(run_id: str, body: ApprovalAction):
    from app.db.connection import get_db
    db = await get_db()
    await db.execute(
        "INSERT INTO approvals (id, run_id, stage, decision, notes) VALUES (?,?,?,?,?)",
        (uuid.uuid4().hex, run_id, body.stage, body.decision, body.notes),
    )
    await db.commit()
    await db.close()
    return {"ok": True}
