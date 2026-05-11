"""Render agent – produce images or video via fal.ai.

Image output  (target_output="image"):
  • Calls fal.ai synchronous image API (flux/schnell) – typically 3-10 s.
  • No FFmpeg required.

Video output  (target_output="video" or unset):
  • Submits each scene to the fal.ai async queue (text-to-video).
  • Single-scene: raw clip is renamed to render.mp4 – no FFmpeg.
  • Multi-scene: raw clips are concatenated with FFmpeg (no re-encode).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import List

import httpx

from app.agents.base import checkpoint, record_step, emit_progress, think
from app.schemas.run import Stage
from app.runtime.event_bus import event_bus

# ── fal.ai endpoints ──────────────────────────────────────────────────────────

FAL_QUEUE_BASE = "https://queue.fal.run"
FAL_SYNC_BASE  = "https://fal.run"

# Image models (synchronous – no polling needed)
IMAGE_MODELS = [
    "fal-ai/flux/schnell",       # fastest, ~3-8 s
    "fal-ai/fast-sdxl",          # fallback
]

# Video models (async queue)
VIDEO_MODELS = [
    "fal-ai/wan/v2.2-a14b/text-to-video",
    "fal-ai/ltx-video/v0.9.1/text-to-video",
    "fal-ai/minimax/hailuo-02/standard/text-to-video",
    "fal-ai/kling-video/v2.5-turbo/pro/text-to-video",
]

FAL_POLL_INTERVAL = 5    # seconds between queue status checks
FAL_VIDEO_TIMEOUT = 900  # 15 min max per clip


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_fal_key() -> str:
    key = (os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or "").strip()
    if not key or key == "YOUR_FAL_KEY_HERE":
        raise RuntimeError("FAL_KEY not set — add your fal.ai API key to Fly secrets.")
    return key


def _target_output(state: dict) -> str:
    """Return 'image' or 'video' from settings."""
    return str((state.get("settings") or {}).get("target_output", "video")).lower()


def _resolve_max_scenes(state: dict) -> int:
    raw = (state.get("settings") or {}).get("max_scenes", 1)
    try:
        return max(1, min(int(raw), 8))
    except (TypeError, ValueError):
        return 1


def _extract_scenes(state: dict, max_scenes: int) -> list:
    sc = state["outputs"].get("script", {})
    scenes = sc.get("scenes", sc.get("script_lines", []))
    if scenes:
        return [
            {
                "title": f"Scene {s.get('id', i + 1)}",
                "body": s.get("text", s.get("description", "")),
                "duration": max(float(s.get("duration_seconds", s.get("duration", 5))), 3),
            }
            for i, s in enumerate(scenes[:max_scenes])
        ]

    plan = state["outputs"].get("planning", {})
    plan_scenes = plan.get("scenes", [])
    if plan_scenes:
        return [
            {
                "title": f"Scene {s.get('id', i + 1)}",
                "body": s.get("description", str(s)),
                "duration": max(float(s.get("duration", 5)), 3),
            }
            for i, s in enumerate(plan_scenes[:max_scenes])
        ]

    sb = state["outputs"].get("storyboard", {})
    if sb.get("shots"):
        return [
            {
                "title": s.get("shot_type", f"Shot {i + 1}"),
                "body": s.get("description", ""),
                "duration": max(float(s.get("duration_seconds", s.get("duration", 5))), 3),
            }
            for i, s in enumerate(sb["shots"][:max_scenes])
        ]

    return [{"title": "Director's Cut", "body": state.get("prompt", ""), "duration": 5}]


# ── Image generation (synchronous) ───────────────────────────────────────────

async def _generate_image(
    prompt: str,
    out_path: str,
    run_id: str,
    stage: str,
) -> str:
    """Call fal.ai image endpoint (synchronous). Returns the local saved path."""
    fal_key = _get_fal_key()
    headers = {"Authorization": f"Key {fal_key}", "Content-Type": "application/json"}

    for model in IMAGE_MODELS:
        url = f"{FAL_SYNC_BASE}/{model}"
        payload = {
            "prompt": prompt,
            "image_size": "landscape_16_9",
            "num_inference_steps": 4,
            "num_images": 1,
            "enable_safety_checker": False,
        }
        await think(run_id, stage, f"Calling fal.ai image API ({model.split('/')[-1]})…")
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code in (401, 403):
                    raise RuntimeError(f"fal.ai auth error {resp.status_code}: {resp.text[:200]}")
                if resp.status_code == 422:
                    # model may not support these params — try next
                    await think(run_id, stage, f"Model {model} rejected payload, trying next…")
                    continue
                resp.raise_for_status()
                data = resp.json()

            images = data.get("images") or data.get("image") or []
            if isinstance(images, dict):
                images = [images]
            if not images:
                raise RuntimeError(f"No images in response: {list(data.keys())}")

            img_url = images[0].get("url") or images[0].get("image_url")
            if not img_url:
                raise RuntimeError(f"No URL in image object: {images[0]}")

            await think(run_id, stage, "Downloading image…")
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as dl:
                r = await dl.get(img_url)
                r.raise_for_status()
                with open(out_path, "wb") as f:
                    f.write(r.content)
            return out_path

        except RuntimeError:
            raise
        except Exception as e:
            await think(run_id, stage, f"Model {model} failed ({e}), trying next…")
            continue

    raise RuntimeError("All image models failed")


# ── Video generation (async queue) ───────────────────────────────────────────

def _video_model() -> str:
    return os.getenv("FAL_VIDEO_MODEL", "").strip() or VIDEO_MODELS[0]


async def _submit_video_job(
    client: httpx.AsyncClient,
    model: str,
    prompt: str,
    fal_key: str,
    duration: str = "5",
) -> dict:
    url = f"{FAL_QUEUE_BASE}/{model}"
    payload = {
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": "16:9",
        "negative_prompt": "blur, distort, low quality, watermark, text",
    }
    headers = {"Authorization": f"Key {fal_key}", "Content-Type": "application/json"}
    resp = await client.post(url, json=payload, headers=headers, timeout=30)
    if resp.status_code in (401, 403):
        raise RuntimeError(f"fal.ai auth/billing error ({resp.status_code}): {resp.text[:200]}")
    resp.raise_for_status()
    data = resp.json()
    if "status_url" not in data or "response_url" not in data:
        raise RuntimeError(f"Unexpected fal.ai submit response: {list(data.keys())}")
    return data


async def _poll_video_job(
    client: httpx.AsyncClient,
    job: dict,
    fal_key: str,
    run_id: str,
    stage: str,
    label: str,
) -> dict:
    headers = {"Authorization": f"Key {fal_key}"}
    elapsed = 0
    while elapsed < FAL_VIDEO_TIMEOUT:
        await asyncio.sleep(FAL_POLL_INTERVAL)
        elapsed += FAL_POLL_INTERVAL

        resp = await client.get(job["status_url"], headers=headers, timeout=15)
        resp.raise_for_status()
        status = resp.json().get("status", "UNKNOWN")

        if status == "COMPLETED":
            res = await client.get(job["response_url"], headers=headers, timeout=15)
            res.raise_for_status()
            return res.json()
        if status in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"fal.ai job {status} for {label}")

        if elapsed % 30 == 0:
            await think(run_id, stage, f"{label} — generating ({elapsed}s elapsed)…")

    raise TimeoutError(f"fal.ai video timed out after {FAL_VIDEO_TIMEOUT}s")


async def _download(client: httpx.AsyncClient, url: str, path: str) -> None:
    async with client.stream("GET", url, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            async for chunk in r.aiter_bytes(65536):
                f.write(chunk)


async def _generate_video_clip(
    prompt: str,
    context: str,
    out_path: str,
    scene_idx: int,
    total: int,
    duration: float,
    run_id: str,
    stage: str,
) -> bool:
    fal_key = _get_fal_key()
    model = _video_model()
    model_lower = model.lower()

    if "minimax" in model_lower or "hailuo" in model_lower:
        fal_dur = "10" if duration > 7 else "6"
    else:
        fal_dur = "5"

    full_prompt = (
        f"Cinematic wide shot: {prompt}. "
        f"Context: {context}. "
        "Photorealistic, dramatic lighting, smooth camera movement."
    )
    label = f"Scene {scene_idx + 1}/{total}"
    short = model.split("/")[-2] if "/" in model else model

    await think(run_id, stage, f"Submitting {label} to fal.ai ({short})…")
    async with httpx.AsyncClient() as client:
        job = await _submit_video_job(client, model, full_prompt, fal_key, fal_dur)
        await think(run_id, stage, f"{label} queued (id: {job.get('request_id','?')[:12]}…)")
        result = await _poll_video_job(client, job, fal_key, run_id, stage, label)

        video_url = None
        if "video" in result:
            video_url = result["video"].get("url")
        elif "output" in result:
            video_url = result["output"].get("video", {}).get("url")
        if not video_url:
            raise RuntimeError(f"No video URL in fal.ai result: {list(result.keys())}")

        await think(run_id, stage, f"Downloading {label}…")
        await _download(client, video_url, out_path)
    return True


# ── FFmpeg (concat only, used only for multi-scene video) ────────────────────

async def _concat_clips(clip_paths: list, out_path: str) -> None:
    """Concatenate clips with ffmpeg stream-copy (fast, no re-encode)."""
    import shutil
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        # If only one clip, just copy it
        if len(clip_paths) == 1:
            import shutil as _sh
            _sh.copy2(clip_paths[0], out_path)
            return
        raise RuntimeError("ffmpeg not found and multiple clips need concatenating")

    list_file = out_path + ".txt"
    with open(list_file, "w") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0", "-i", list_file,
        "-c", "copy",          # stream copy — no re-encode, very fast
        "-movflags", "+faststart",
        out_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    os.remove(list_file)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg concat failed: {stderr.decode()[-400:]}")


# ── Main render node ──────────────────────────────────────────────────────────

async def render_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage  = Stage.RENDER.value
    await emit_progress(run_id, stage, "Starting render…")

    try:
        _get_fal_key()
    except RuntimeError as e:
        await think(run_id, stage, f"⚠️ {e}")
        state["outputs"][stage] = {"rendered": False, "error": str(e)}
        state["current_stage"] = Stage.PACKAGE.value
        await record_step(run_id, stage, "failed", state["outputs"][stage], error=str(e))
        await checkpoint(state, stage)
        return state

    export_dir = Path("data") / "exports" / run_id
    export_dir.mkdir(parents=True, exist_ok=True)

    target = _target_output(state)
    prompt_context = state.get("prompt", "")
    scenes = _extract_scenes(state, _resolve_max_scenes(state))

    # ── IMAGE output path ──────────────────────────────────────────────────
    if target == "image":
        await think(run_id, stage, f"Generating image via fal.ai (fast mode)…")
        img_path = str(export_dir / "render.jpg")
        scene_prompt = scenes[0]["body"] if scenes else prompt_context

        full_prompt = (
            f"{scene_prompt}. "
            f"Context: {prompt_context}. "
            "High quality, photorealistic, cinematic composition, sharp focus."
        )

        try:
            await _generate_image(full_prompt, img_path, run_id, stage)
        except Exception as e:
            err = str(e)[:300]
            state["outputs"][stage] = {"rendered": False, "error": err}
            state["current_stage"] = Stage.PACKAGE.value
            await record_step(run_id, stage, "failed", state["outputs"][stage], error=err)
            await checkpoint(state, stage)
            await emit_progress(run_id, stage, f"Image render failed: {err}")
            return state

        file_size = os.path.getsize(img_path) if os.path.exists(img_path) else 0
        out_rel = f"data/exports/{run_id}/render.jpg"
        state["outputs"][stage] = {
            "rendered": True,
            "output_path": out_rel,
            "file_size_bytes": file_size,
            "scene_count": 1,
            "model": IMAGE_MODELS[0],
            "kind": "image",
        }
        state["current_stage"] = Stage.PACKAGE.value

        await record_step(run_id, stage, "completed", state["outputs"][stage])
        await checkpoint(state, stage)
        await emit_progress(run_id, stage, "Image render complete ✓")
        return state

    # ── VIDEO output path ──────────────────────────────────────────────────
    await think(run_id, stage,
                f"Generating {len(scenes)} video clip(s) via fal.ai "
                f"({_video_model().split('/')[-2]})…")

    raw_paths: List[str] = []
    for i, scene in enumerate(scenes):
        raw_path = str(export_dir / f"_raw_{i:03d}.mp4")
        try:
            await _generate_video_clip(
                scene["body"], prompt_context, raw_path,
                i, len(scenes), scene["duration"],
                run_id, stage,
            )
            if os.path.exists(raw_path) and os.path.getsize(raw_path) > 1000:
                raw_paths.append(raw_path)
                await event_bus.emit(run_id, "stage_progress", {
                    "stage": stage,
                    "message": f"Clip {i + 1} ready",
                    "preview_clip_url": f"/media/exports/{run_id}/_raw_{i:03d}.mp4",
                    "preview_scene_index": i,
                    "preview_scene_total": len(scenes),
                })
        except Exception as e:
            err_msg = str(e)[:200]
            await think(run_id, stage, f"Scene {i + 1} failed: {err_msg}")
            if "auth/billing" in err_msg.lower() or "exhausted" in err_msg.lower():
                state["outputs"][stage] = {"rendered": False, "error": err_msg}
                state["current_stage"] = Stage.PACKAGE.value
                await record_step(run_id, stage, "failed", state["outputs"][stage], error=err_msg)
                await checkpoint(state, stage)
                return state

    if not raw_paths:
        err = "All video generations failed"
        state["outputs"][stage] = {"rendered": False, "error": err}
        state["current_stage"] = Stage.PACKAGE.value
        await record_step(run_id, stage, "failed", state["outputs"][stage])
        await checkpoint(state, stage)
        return state

    # Assemble final video (no FFmpeg re-encode; stream-copy only for multi-scene)
    final_path = str(export_dir / "render.mp4")
    await think(run_id, stage, "Assembling final video…")

    if len(raw_paths) == 1:
        os.rename(raw_paths[0], final_path)
    else:
        try:
            await _concat_clips(raw_paths, final_path)
        except Exception as e:
            # Fallback: just use the first clip
            await think(run_id, stage, f"Concat failed ({e}), using first clip as output")
            os.rename(raw_paths[0], final_path)

    file_size = os.path.getsize(final_path) if os.path.exists(final_path) else 0
    await think(run_id, stage, f"Video ready: {file_size / 1024 / 1024:.1f} MB")

    state["outputs"][stage] = {
        "rendered": True,
        "output_path": f"data/exports/{run_id}/render.mp4",
        "duration_seconds": sum(s["duration"] for s in scenes),
        "file_size_bytes": file_size,
        "scene_count": len(scenes),
        "preview_clip_paths": [f"data/exports/{run_id}/_raw_{i:03d}.mp4" for i in range(len(raw_paths))],
        "model": _video_model(),
        "kind": "video",
    }
    state["current_stage"] = Stage.PACKAGE.value

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Video render complete ✓")
    return state
