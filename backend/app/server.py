"""FastAPI server – the HTTP surface that Tauri talks to."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Director's Cut", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import routes safely – each one handles its own import errors
from app.routes import projects, runs, artifacts, approvals, settings, events  # noqa: E402

app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(runs.router, prefix="/api/runs", tags=["runs"])
app.include_router(artifacts.router, prefix="/api/artifacts", tags=["artifacts"])
app.include_router(approvals.router, prefix="/api/approvals", tags=["approvals"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(events.router, prefix="/api/events", tags=["events"])


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


@app.on_event("startup")
async def startup():
    """Initialize DB on startup."""
    from app.db.connection import get_db
    db = await get_db()
    await db.close()
    print("✅ Director's Cut backend ready on http://127.0.0.1:9420")
