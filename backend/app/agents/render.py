"""Render agent – produce video from storyboard using Pillow frames + FFmpeg.

Generates visually rich frames with gradients, text, animated elements using
Pillow, then pipes them into FFmpeg as a raw image sequence.  No drawtext or
libass filter required — all rendering happens in Python.
"""
from __future__ import annotations

import asyncio
import math
import os
import shutil
import textwrap
from pathlib import Path
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

from app.agents.base import checkpoint, record_step, emit_progress, think
from app.schemas.run import Stage

# ── Visual theme ────────────────────────────────────────────────────────────

# Rich gradient palettes per scene (top-colour, bottom-colour)
SCENE_PALETTES: List[Tuple[Tuple[int,...], Tuple[int,...]]] = [
    ((15, 12, 41),   (48, 43, 99)),    # deep indigo
    ((20, 30, 48),   (36, 59, 85)),    # midnight blue
    ((44, 62, 80),   (52, 152, 219)),  # ocean
    ((72, 52, 117),  (153, 51, 153)),  # purple haze
    ((233, 69, 96),  (72, 52, 117)),   # sunset magenta
    ((13, 115, 119), (20, 167, 108)),  # teal forest
    ((30, 60, 114),  (42, 82, 152)),   # steel blue
    ((245, 166, 35), (233, 69, 96)),   # amber fire
]

ACCENT = (255, 255, 255)
DIM_WHITE = (200, 200, 200)
WIDTH, HEIGHT, FPS = 1920, 1080, 30


# ── Helpers ─────────────────────────────────────────────────────────────────

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


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Try system fonts, fall back to default."""
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _lerp_color(c1: Tuple[int,...], c2: Tuple[int,...], t: float) -> Tuple[int,...]:
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def _draw_gradient(img: Image.Image, top: Tuple[int,...], bottom: Tuple[int,...]):
    draw = ImageDraw.Draw(img)
    for y in range(img.height):
        t = y / max(img.height - 1, 1)
        draw.line([(0, y), (img.width, y)], fill=_lerp_color(top, bottom, t))


def _draw_particles(draw: ImageDraw.Draw, frame: int, count: int = 40):
    import random
    rng = random.Random(42)
    for _ in range(count):
        bx, by = rng.randint(0, WIDTH), rng.randint(0, HEIGHT)
        speed = rng.uniform(0.3, 1.5)
        size = rng.randint(1, 4)
        alpha_base = rng.randint(40, 120)
        y = (by - int(frame * speed)) % HEIGHT
        x = bx + int(math.sin(frame * 0.02 + bx) * 20)
        alpha = int(alpha_base * (0.5 + 0.5 * math.sin(frame * 0.05 + bx)))
        draw.ellipse([x - size, y - size, x + size, y + size],
                     fill=(255, 255, 255, max(0, min(255, alpha))))


def _draw_scene_number(draw: ImageDraw.Draw, idx: int, total: int):
    font = _get_font(24)
    text = f"SCENE {idx + 1} / {total}"
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text((WIDTH - (bbox[2] - bbox[0]) - 40, 30), text, fill=(255, 255, 255, 140), font=font)


def _draw_progress_bar(draw: ImageDraw.Draw, progress: float):
    y = HEIGHT - 6
    draw.rectangle([0, y, WIDTH, y + 4], fill=(255, 255, 255, 30))
    draw.rectangle([0, y, int(WIDTH * progress), y + 4], fill=(255, 255, 255, 100))


def _render_frame(
    scene_idx: int, total_scenes: int,
    title: str, body: str,
    frame_in_scene: int, total_frames: int,
    palette: Tuple[Tuple[int,...], Tuple[int,...]],
) -> bytes:
    """Render one frame as raw RGB bytes."""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    t = frame_in_scene / max(total_frames - 1, 1)

    # Animated gradient
    top = _lerp_color(palette[0], palette[1], t * 0.15)
    bot = _lerp_color(palette[1], palette[0], t * 0.15)
    _draw_gradient(img, top, bot)

    img = img.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    _draw_particles(draw, frame_in_scene)
    _draw_scene_number(draw, scene_idx, total_scenes)

    # Title with fade-in
    title_font = _get_font(64, bold=True)
    fade = min(1.0, frame_in_scene / (FPS * 0.5)) if total_frames > FPS else 1.0
    title_alpha = int(255 * fade)
    wrapped_title = textwrap.fill(title, width=35)
    bbox = draw.textbbox((0, 0), wrapped_title, font=title_font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx, ty = (WIDTH - tw) // 2, int(HEIGHT * 0.22) - th // 2
    draw.text((tx + 3, ty + 3), wrapped_title, fill=(0, 0, 0, title_alpha // 2), font=title_font)
    draw.text((tx, ty), wrapped_title, fill=(255, 255, 255, title_alpha), font=title_font)

    # Decorative line
    line_y = ty + th + 20
    line_w = min(tw + 40, int(WIDTH * 0.6))
    line_x = (WIDTH - line_w) // 2
    line_progress = min(1.0, frame_in_scene / (FPS * 0.8))
    draw.rectangle([line_x, line_y, line_x + int(line_w * line_progress), line_y + 3],
                   fill=(255, 255, 255, int(180 * fade)))

    # Body text with fade-in
    body_font = _get_font(36)
    body_fade = min(1.0, max(0, (frame_in_scene - FPS * 0.3)) / (FPS * 0.5))
    if body and body_fade > 0:
        body_alpha = int(220 * body_fade)
        wrapped = textwrap.fill(body, width=55)
        lines = wrapped.split("\n")[:8]
        body_text = "\n".join(lines)
        bb = draw.textbbox((0, 0), body_text, font=body_font)
        bw, bh = bb[2] - bb[0], bb[3] - bb[1]
        bx, by = (WIDTH - bw) // 2, int(HEIGHT * 0.48)
        pad = 30
        draw.rounded_rectangle([bx - pad, by - pad, bx + bw + pad, by + bh + pad],
                               radius=12, fill=(0, 0, 0, int(120 * body_fade)))
        draw.text((bx, by), body_text, fill=(220, 220, 220, body_alpha), font=body_font)

    _draw_progress_bar(draw, (scene_idx + t) / total_scenes)

    img = Image.alpha_composite(img, overlay).convert("RGB")
    return img.tobytes()


async def _generate_scene_clip(
    ffmpeg: str, out_path: str,
    scene_idx: int, total_scenes: int,
    title: str, body: str,
    duration: float,
    palette: Tuple[Tuple[int,...], Tuple[int,...]],
) -> None:
    """Generate a scene clip by piping Pillow frames into FFmpeg."""
    total_frames = int(duration * FPS)

    cmd = [
        ffmpeg, "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS),
        "-i", "pipe:0",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        "-movflags", "+faststart",
        out_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )

    loop = asyncio.get_event_loop()
    for f in range(total_frames):
        frame_bytes = await loop.run_in_executor(
            None, _render_frame, scene_idx, total_scenes, title, body, f, total_frames, palette,
        )
        try:
            proc.stdin.write(frame_bytes)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            break

    proc.stdin.close()
    await proc.stdin.wait_closed()
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg scene failed ({proc.returncode}): {stderr.decode()[-500:]}")


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
    for p in clip_paths:
        try:
            os.remove(p)
        except OSError:
            pass


def _extract_scenes(state: dict) -> list:
    sb = state["outputs"].get("storyboard", {})
    if sb.get("shots"):
        return [
            {"title": f"Shot {s.get('scene_id', i+1)}",
             "body": s.get("description", ""),
             "duration": max(float(s.get("duration_seconds", s.get("duration", 4))), 2)}
            for i, s in enumerate(sb["shots"])
        ]

    sc = state["outputs"].get("script", {})
    scenes = sc.get("scenes", sc.get("script_lines", []))
    if scenes:
        return [
            {"title": f"Scene {s.get('id', i+1)}",
             "body": s.get("text", s.get("description", "")),
             "duration": max(float(s.get("duration_seconds", s.get("duration", 5))), 2)}
            for i, s in enumerate(scenes)
        ]

    plan = state["outputs"].get("planning", {})
    plan_scenes = plan.get("scenes", [])
    if plan_scenes:
        return [
            {"title": f"Scene {s.get('id', i+1)}",
             "body": s.get("description", str(s)),
             "duration": max(float(s.get("duration", 5)), 2)}
            for i, s in enumerate(plan_scenes)
        ]

    return [{"title": "Director's Cut", "body": state.get("prompt", "Video production"), "duration": 5}]


async def render_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.RENDER.value
    await emit_progress(run_id, stage, "Rendering video…")

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

    await think(run_id, stage, f"Using FFmpeg at: {ffmpeg}")

    export_dir = Path("data") / "exports" / run_id
    export_dir.mkdir(parents=True, exist_ok=True)
    final_path = str(export_dir / "render.mp4")

    scenes = _extract_scenes(state)
    await think(run_id, stage, f"Rendering {len(scenes)} scenes with Pillow → FFmpeg pipeline…")

    clip_paths = []
    for i, scene in enumerate(scenes):
        palette = SCENE_PALETTES[i % len(SCENE_PALETTES)]
        clip_path = str(export_dir / f"_clip_{i:03d}.mp4")
        await think(run_id, stage, f"Encoding scene {i+1}/{len(scenes)}: {scene['title']}", delay=0.3)
        try:
            await _generate_scene_clip(
                ffmpeg, clip_path, i, len(scenes),
                scene["title"], scene["body"],
                max(scene["duration"], 2),
                palette,
            )
            clip_paths.append(clip_path)
        except RuntimeError as e:
            await think(run_id, stage, f"⚠️ Scene {i+1} failed: {str(e)[:200]}")

    if not clip_paths:
        state["outputs"][stage] = {"rendered": False, "error": "All scene renders failed"}
        state["current_stage"] = Stage.PACKAGE.value
        await record_step(run_id, stage, "failed", state["outputs"][stage])
        await checkpoint(state, stage)
        return state

    await think(run_id, stage, "Concatenating clips into final video…", delay=0.5)
    if len(clip_paths) == 1:
        os.rename(clip_paths[0], final_path)
    else:
        await _concat_clips(ffmpeg, clip_paths, final_path)

    total_dur = sum(s["duration"] for s in scenes)
    file_size = os.path.getsize(final_path) if os.path.exists(final_path) else 0

    await think(run_id, stage, f"✅ Video rendered: {total_dur:.0f}s, {file_size/1024:.0f} KB")

    state["outputs"][stage] = {
        "rendered": True,
        "output_path": f"data/exports/{run_id}/render.mp4",
        "duration_seconds": total_dur,
        "file_size_bytes": file_size,
        "scene_count": len(scenes),
    }
    state["current_stage"] = Stage.PACKAGE.value

    await record_step(run_id, stage, "completed", state["outputs"][stage])
    await checkpoint(state, stage)
    await emit_progress(run_id, stage, "Render complete ✓")
    return state
