from __future__ import annotations

import json
import os
import asyncio
import re
from typing import Optional
import httpx
from app.runtime.logger import get_logger

log = get_logger("llm")

# ── Provider configs ────────────────────────────────────────────────────────
PROVIDERS = {
    "groq": {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "env_key": "GROQ_API_KEY",
        "default_model": "llama-3.3-70b-versatile",
    },
    "openrouter": {
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "env_key": "OPENROUTER_API_KEY",
        "default_model": "openai/gpt-4o-mini",
    },
}


def _resolve_provider(settings: dict) -> tuple:
    """Pick the first provider that has an API key configured."""
    # Check Groq first (free tier)
    groq_key = (settings.get("groq_api_key") or os.getenv("GROQ_API_KEY") or "").strip()
    if groq_key:
        p = PROVIDERS["groq"]
        model = settings.get("model") or os.getenv("DIRECTOR_MODEL") or p["default_model"]
        # Groq only supports its own models — remap if user has an openai/ model set
        if "/" in model:
            model = p["default_model"]
        return p["url"], groq_key, model

    # Then OpenRouter
    or_key = (settings.get("openrouter_api_key") or os.getenv("OPENROUTER_API_KEY") or "").strip()
    if or_key:
        p = PROVIDERS["openrouter"]
        model = settings.get("model") or os.getenv("DIRECTOR_MODEL") or p["default_model"]
        return p["url"], or_key, model

    return None, None, None


async def call_llm(
    system: str,
    user: str,
    model: Optional[str] = None,
    settings: Optional[dict] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> dict:
    settings = settings or {}
    url, api_key, resolved_model = _resolve_provider(settings)
    if model:
        resolved_model = model

    if not api_key:
        log.warning("no_api_key", message="No LLM API key configured – returning simulated response")
        return _simulated_response(system, user)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # OpenRouter wants a referer header
    if "openrouter" in url:
        headers["HTTP-Referer"] = "https://director-cut.local"

    payload = {
        "model": resolved_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    # Groq supports json mode on compatible models
    if "groq" in url or "openrouter" in url:
        payload["response_format"] = {"type": "json_object"}

    log.info("llm_call", provider=url.split("/")[2], model=resolved_model)
    print(f"[LLM] Calling {url.split('/')[2]} model={resolved_model} ...", flush=True)

    try:
        timeout_cfg = httpx.Timeout(60.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout_cfg) as client:
            # Retry loop for rate limits (429)
            max_retries = 4
            for attempt in range(max_retries):
                resp = await client.post(url, json=payload, headers=headers)
                print(f"[LLM] Response status: {resp.status_code}", flush=True)
                if resp.status_code == 429 and attempt < max_retries - 1:
                    # Parse retry-after hint from error message
                    wait = 3.0
                    try:
                        m = re.search(r"try again in (\d+\.?\d*)", resp.text)
                        if m:
                            wait = max(float(m.group(1)) + 0.5, 1.0)
                    except Exception:
                        pass
                    wait = min(wait, 15.0)
                    print(f"[LLM] Rate limited, retrying in {wait:.1f}s (attempt {attempt+1}/{max_retries})…", flush=True)
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break

        content = data["choices"][0]["message"]["content"]
        print(f"[LLM] Got {len(content)} chars", flush=True)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            log.warning("llm_non_json_response", content=content[:200])
            return {"raw": content}
    except httpx.TimeoutException as exc:
        print(f"[LLM] TIMEOUT: {exc}", flush=True)
        log.error("llm_timeout", error=str(exc))
        return _simulated_response(system, user)
    except httpx.HTTPStatusError as exc:
        print(f"[LLM] HTTP ERROR {exc.response.status_code}: {exc.response.text[:300]}", flush=True)
        log.error("llm_http_error", status=exc.response.status_code, error=exc.response.text[:300])
        return _simulated_response(system, user)
    except Exception as exc:
        print(f"[LLM] ERROR: {type(exc).__name__}: {exc}", flush=True)
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
