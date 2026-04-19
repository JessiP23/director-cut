"""Render agent – produce video with AI-generated clips via fal.ai + FFmpeg.

For each scene we:
1. Call fal.ai text-to-video API to generate a real video clip
2. Download the resulting MP4
3. Concatenate all clips with FFmpeg into the final render

Supports multiple fal.ai models with automatic fallback.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import List

import httpx

from app.agents.base import checkpoint, record_step, emit_progress, think
from app.schemas.run import Stage
from app.runtime.event_bus import event_bus

# fal.ai configuration
FAL_API_BASE = "https://queue.fal.run"
# Model priority – user can override via FAL_VIDEO_MODEL env var.
DEFAULT_MODELS = [
    "fal-ai/minimax/hailuo-02/standard/text-to-video",
    "fal-ai/kling-video/v2.5-turbo/pro/text-to-video",
    "fal-ai/wan/v2.2-a14b/text-to-video",
]
FAL_POLL_INTERVAL = 5   # seconds between status checks
FAL_TIMEOUT = 600        # max wait per clip (10 min)


# ── FFmpeg discovery ────────────────────────────────────────────────────────

def _find_ffmpeg() -> str:
    custom = os.getenv("FFMPEG_PATH", "").strip()
    if custom and shutil.which(custom):
        return custom
    for candidate in ("ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"):
        if shutil.which(candidate):
            return candidate
    raise FileNotFoundError(
        "FFmpeg not found. Install it (brew install ffmpeg) or set FFMPEG_PATH."
    )


# ── fal.ai helpers ──────────────────────────────────────────────────────────

def _get_fal_key() -> str:
    key = (os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or "").strip()
    if not key or key == "YOUR_FAL_KEY_HERE":
        raise RuntimeError("FAL_KEY not set in .env — add your fal.ai API key.")
    return key


def _get_model() -> str:
    return os.getenv("FAL_VIDEO_MODEL", "").strip() or DEFAULT_MODELS[0]


def _resolve_max_scenes(state: dict) -> int:
    raw = (state.get("settings") or {}).get("max_scenes", 4)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 4
    return max(1, min(value, 8))


async def _submit_video_job(
    client: httpx.AsyncClient,
    model: str,
    prompt: str,
    fal_key: str,
    duration: str = "5",
    aspect_ratio: str = "16:9",
) -> dict:
    """Submit a text-to-video job to fal.ai queue. Returns job info dict with URLs."""
    url = f"{FAL_API_BASE}/{model}"
    payload = {
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "negative_prompt": "blur, distort, low quality, watermark, text overlay",
    }
    headers = {
        "Authorization": f"Key {fal_key}",
        "Content-Type": "application/json",
    }
    resp = await client.post(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()  # has request_id, status_url, response_url


async def _poll_until_done(
    client: httpx.AsyncClient,
    job_info: dict,
    fal_key: str,
    run_id: str,
    stage: str,
    scene_label: str,
) -> dict:
    """Poll fal.ai queue until the job completes. Returns the result dict."""
    status_url = job_info["status_url"]
    result_url = job_info["response_url"]
    headers = {"Authorization": f"Key {fal_key}"}

    elapsed = 0
    while elapsed < FAL_TIMEOUT:
        await asyncio.sleep(FAL_POLL_INTERVAL)
        elapsed += FAL_POLL_INTERVAL

        resp = await client.get(status_url, headers=headers, timeout=15)
        resp.raise_for_status()
        status_data = resp.json()
        status = status_data.get("status", "UNKNOWN")

        if status == "COMPLETED":
            res = await client.get(result_url, headers=headers, timeout=15)
            res.raise_for_status()
            return res.json()
        elif status in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"fal.ai job {status}: {status_data}")

        if elapsed % 15 == 0:
            await think(run_id, stage, f"{scene_label} — waiting ({elapsed}s)…")

    raise TimeoutError(f"fal.ai job timed out after {FAL_TIMEOUT}s")


async def _download_video(client: httpx.AsyncClient, video_url: str, out_path: str) -> None:
    """Download a video file from a URL."""
    async with client.stream("GET", video_url, timeout=60, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(out_path, "wb") as f:
            async for chunk in resp.aiter_bytes(chunk_size=65536):
                f.write(chunk)


async def _generate_scene_video(
    scene_prompt: str,
    context: str,
    out_path: str,
    scene_idx: int,
    total_scenes: int,
    duration: float,
    run_id: str,
    stage: str,
) -> bool:
    """Generate a video clip for one scene via fal.ai. Returns True on success."""
    fal_key = _get_fal_key()
    model = _get_model()

    full_prompt = (
        f"Cinematic wide shot: {scene_prompt}. "
        f"Context: {context}. "
        f"Photorealistic, dramatic lighting, smooth camera movement, high production value."
    )

    # fal.ai duration depends on model — minimax uses "6" or "10", kling uses "5" or "10"
    model_lower = model.lower()
    if "minimax" in model_lower or "hailuo" in model_lower:
        fal_duration = "10" if duration > 7 else "6"
    else:
        fal_duration = "10" if duration > 7 else "5"

    async with httpx.AsyncClient() as client:
        scene_label = f"Scene {scene_idx + 1}/{total_scenes}"
        short_model = model.split("/")[-2] if "/" in model else model

        await think(run_id, stage, f"Submitting {scene_label} to fal.ai ({short_model})…")

        job_info = await _submit_video_job(
            client, model, full_prompt, fal_key, duration=fal_duration,
        )
        request_id = job_info.get("request_id", "???")
        await think(run_id, stage, f"{scene_label} queued (id: {request_id[:12]}…)")

        result = await _poll_until_done(
            client, job_info, fal_key, run_id, stage, scene_label,
        )

        # Extract video URL from result
        video_url = None
        if "video" in result:
            video_url = result["video"].get("url")
        elif "output" in result:
            video_url = result["output"].get("video", {}).get("url")

        if not video_url:
            raise RuntimeError(f"No video URL in fal.ai response: {list(result.keys())}")

        await think(run_id, stage, f"Downloading {scene_label} video…")
        await _download_video(client, video_url, out_path)
        return True


# ── Normalize + Concat ──────────────────────────────────────────────────────

async def _normalize_clip(ffmpeg: str, in_path: str, out_path: str) -> None:
    """Re-encode a clip to consistent format for concatenation."""
    cmd = [
        ffmpeg, "-y", "-i", in_path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", "30", "-s", "1920x1080",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart",
        out_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg normalize failed: {stderr.decode()[-300:]}")


async def _concat_clips(ffmpeg: str, clip_paths: list, out_path: str) -> None:
    list_file = out_path + ".txt"
    with open(list_file, "w") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0", "-i", list_file,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-movflags", "+faststart",
        out_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg concat failed ({proc.returncode}): {stderr.decode()[-500:]}")

    os.remove(list_file)


# ── Extract scenes from pipeline state ──────────────────────────────────────

def _extract_scenes(state: dict) -> list:
    max_scenes = _resolve_max_scenes(state)

    sc = state["outputs"].get("script", {})
    scenes = sc.get("scenes", sc.get("script_lines", []))
    if scenes:
        extracted = [
            {"title": f"Scene {s.get('id', i+1)}",
             "body": s.get("text", s.get("description", "")),
             "duration": max(float(s.get("duration_seconds", s.get("duration", 5))), 3)}
            for i, s in enumerate(scenes)
        ]
        return extracted[:max_scenes]

    plan = state["outputs"].get("planning", {})
    plan_scenes = plan.get("scenes", [])
    if plan_scenes:
        extracted = [
            {"title": f"Scene {s.get('id', i+1)}",
             "body": s.get("description", str(s)),
             "duration": max(float(s.get("duration", 5)), 3)}
            for i, s in enumerate(plan_scenes)
        ]
        return extracted[:max_scenes]

    sb = state["outputs"].get("storyboard", {})
    if sb.get("shots"):
        extracted = [
            {"title": s.get("shot_type", f"Shot {i+1}"),
             "body": s.get("description", ""),
             "duration": max(float(s.get("duration_seconds", s.get("duration", 5))), 3)}
            for i, s in enumerate(sb["shots"])
        ]
        return extracted[:max_scenes]

    return [{"title": "Director's Cut", "body": state.get("prompt", "Video production"), "duration": 5}][:max_scenes]


# ── Main render node ────────────────────────────────────────────────────────

async def render_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.RENDER.value
    prompt_context = state.get("prompt", "video production")
    await emit_progress(run_id, stage, "Rendering video…")

    # ── Check FFmpeg ──
    await think(run_id, stage, "Locating FFmpeg binary…")
    try:
        ffmpeg = _find_ffmpeg()
    except FileNotFoundError as e:
        await think(run_id, stage, f"⚠️ {e}")
        state["outputs"][stage] = {"rendered": False, "error": str(e)}
        state["current_stage"] = Stage.PACKAGE.value
        await record_step(run_id, stage, "failed", state["outputs"][stage], error=str(e))
        await checkpoint(state, stage)
        await emit_progress(run_id, stage, f"Render skipped — {e}")
        return state

    # ── Check FAL_KEY ──
    try:
        _get_fal_key()
    except RuntimeError as e:
        await think(run_id, stage, f"⚠️ {e}")
        state["outputs"][stage] = {"rendered": False, "error": str(e)}
        state["current_stage"] = Stage.PACKAGE.value
        await record_step(run_id, stage, "failed", state["outputs"][stage], error=str(e))
        await checkpoint(state, stage)
        await emit_progress(run_id, stage, str(e))
        return state

    await think(run_id, stage, f"Using FFmpeg at: {ffmpeg}")

    export_dir = Path("data") / "exports" / run_id
    export_dir.mkdir(parents=True, exist_ok=True)
    final_path = str(export_dir / "render.mp4")

    scenes = _extract_scenes(state)
    max_scenes = _resolve_max_scenes(state)
    model = _get_model()
    short_model = model.split("/")[-2] if "/" in model else model
    await think(run_id, stage,
                f"Generating {len(scenes)} AI video clips via fal.ai ({short_model}), max scenes={max_scenes}…")

    # ── Generate video clips for each scene ──
    raw_paths: List[str] = []
    for i, scene in enumerate(scenes):
        raw_path = str(export_dir / f"_raw_{i:03d}.mp4")
        try:
            success = await _generate_scene_video(
                scene["body"], prompt_context, raw_path,
                i, len(scenes), scene["duration"],
                run_id, stage,
            )
            if success and os.path.exists(raw_path) and os.path.getsize(raw_path) > 1000:
                await think(run_id, stage, f"Scene {i+1} video generated")
                raw_paths.append(raw_path)
                await event_bus.emit(run_id, "stage_progress", {
                    "stage": stage,
                    "message": f"Preview ready for scene {i+1}",
                    "preview_clip_url": f"http://127.0.0.1:9420/media/exports/{run_id}/_raw_{i:03d}.mp4",
                    "preview_scene_index": i,
                    "preview_scene_total": len(scenes),
                })
            else:
                await think(run_id, stage, f"Scene {i+1} — empty or missing video")
        except Exception as e:
            await think(run_id, stage, f"Scene {i+1} failed: {str(e)[:200]}")

    if not raw_paths:
        state["outputs"][stage] = {"rendered": False, "error": "All scene video generations failed"}
        state["current_stage"] = Stage.PACKAGE.value
        await record_step(run_id, stage, "failed", state["outputs"][stage])
        await checkpoint(state, stage)
        return state

    # ── Normalize clips to consistent format for concat ──
    clip_paths: List[str] = []
    for i, raw in enumerate(raw_paths):
        norm_path = str(export_dir / f"_norm_{i:03d}.mp4")
        await think(run_id, stage, f"Normalizing clip {i+1}/{len(raw_paths)}…")
        try:
            await _normalize_clip(ffmpeg, raw, norm_path)
            clip_paths.append(norm_path)
        except Exception as e:
            await think(run_id, stage, f"Normalize failed clip {i+1}: {str(e)[:150]}")
            clip_paths.append(raw)  # fallback to raw

    # ── Concatenate ──
    await think(run_id, stage, "Concatenating clips into final video…", delay=0.5)
    if len(clip_paths) == 1:
        os.rename(clip_paths[0], final_path)
    else:
        await _concat_clips(ffmpeg, clip_paths, final_path)

    total_dur = sum(s["duration"] for s in scenes)
    file_size = os.path.getsize(final_path) if os.path.exists(final_path) else 0

    await think(run_id, stage, f"Video rendered: {len(scenes)} scenes, {file_size/1024/1024:.1f} MB")

    state["outputs"][stage] = {
        "rendered": True,
        "output_path": f"data/exports/{run_id}/render.mp4",
        "duration_seconds": total_dur,
        "file_size_bytes": file_size,
        "scene_count": len(scenes),
        "preview_clip_paths": [f"data/exports/{run_id}/_raw_{i:03d}.mp4" for i in range(len(raw_paths))],
        "model": model,
    }
    state["current_stage"] = Stage.PACKAGE.value

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Render complete ✓")
    return state
