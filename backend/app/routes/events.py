"""Server-Sent Events for live progress streaming to the UI."""

import asyncio
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from app.runtime.event_bus import event_bus

router = APIRouter()


@router.get("/stream/{run_id}")
async def stream_events(run_id: str):
    async def generator():
        queue = event_bus.subscribe(run_id)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {event}\n\n"
        finally:
            event_bus.unsubscribe(run_id, queue)

    return StreamingResponse(generator(), media_type="text/event-stream")
