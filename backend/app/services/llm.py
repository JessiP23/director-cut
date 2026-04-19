"""LLM gateway – all model calls go through here (OpenRouter)."""
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
    """Call an LLM via OpenRouter and return parsed JSON."""
    settings = settings or {}
    model = model or settings.get("model", os.getenv("DIRECTOR_MODEL", "openai/gpt-4o-mini"))
    api_key = settings.get("openrouter_api_key", os.getenv("OPENROUTER_API_KEY", ""))

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
