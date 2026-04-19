from __future__ import annotations

import json
import os
from typing import Optional
import httpx
from app.runtime.logger import get_logger

log = get_logger("llm")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


async def call_llm(
    system: str,
    user: str,
    model: Optional[str] = None,
    settings: Optional[dict] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> dict:
    settings = settings or {}
    model = model or settings.get("model", os.getenv("DIRECTOR_MODEL", "openai/gpt-4o-mini"))
    api_key = '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'

    # ── No key → return a realistic placeholder so the pipeline keeps going ──
    if not api_key:
        log.warning("no_api_key", message="No OpenRouter API key – returning simulated response")
        return _simulated_response(system, user)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://director-cut.local",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(OPENROUTER_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            log.warning("llm_non_json_response", content=content[:200])
            return {"raw": content}
    except Exception as exc:
        log.error("llm_call_failed", error=str(exc))
        return _simulated_response(system, user)


def _simulated_response(system: str, user: str) -> dict:
    """Generate a plausible placeholder when no LLM is available."""
    prompt_snippet = user[:80] if user else "video project"
    return {
        "simulated": True,
        "title": f"Production: {prompt_snippet}",
        "target_length_seconds": 60,
        "tone": "professional",
        "style": "cinematic",
        "scenes": [
            {"id": 1, "description": "Opening – hook the viewer", "duration": 5},
            {"id": 2, "description": "Main content – core message", "duration": 40},
            {"id": 3, "description": "Closing – call to action", "duration": 15},
        ],
        "script_lines": [
            {"scene": 1, "text": "Welcome to this production.", "duration": 5},
            {"scene": 2, "text": "Here is the main content based on your prompt.", "duration": 40},
            {"scene": 3, "text": "Thanks for watching.", "duration": 15},
        ],
        "notes": "Simulated response – set your OpenRouter API key in Settings for real AI output.",
    }
