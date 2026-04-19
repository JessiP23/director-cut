"""Director's Cut – Backend entry point.

Usage:
  uvicorn main:app --reload --port 9420
  OR
  python main.py
"""

import os
import sys

# .env is loaded inside app.server — no extra dependency needed

# Re-export the FastAPI app so `uvicorn main:app` works
from app.server import app  # noqa: E402, F401


def main() -> None:
    import uvicorn
    port = int(os.getenv("DIRECTOR_PORT", "9420"))
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=port,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()