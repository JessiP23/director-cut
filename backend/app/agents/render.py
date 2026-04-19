"""Render agent – invoke FFmpeg to produce a real output video.

Generates colour-slide scenes with text overlays from the storyboard / script,
then concatenates them into a single MP4.  Works without any external media
files — pure FFmpeg filter-graph magic.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import textwrap
from pathlib import Path

from app.agents.base import checkpoint, record_step, emit_progress, think
from app.schemas.run import Stage

# Colour palette for scene backgrounds (looped)
SCENE_COLOURS = [
    "0x1a1a2e", "0x16213e", "0x0f3460", "0x533483",
    "0xe94560", "0x0d7377", "0x14a76c", "0xf5a623",
]


def _find_ffmpeg() -> str:
    """Return the path to ffmpeg, preferring the user setting."""
    custom = os.getenv("FFMPEG_PATH", "").strip()
    if custom and shutil.which(custom):
        return custom
    for candidate in ("ffmpeg", "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"):
        if shutil.which(candidate):
            return candidate
    raise FileNotFoundError(
        "FFmpeg not found. Install it (brew install ffmpeg) or set FFMPEG_PATH."
    )


def _escape_ffmpeg_text(text: str) -> str:
    """Escape special chars for FFmpeg drawtext filter."""
    for ch in ("\\", "'", ":", "%"):
        text = text.replace(ch, f"\\{ch}")
    return text


def _wrap_text(text: str, width: int = 40) -> str:
    """Word-wrap and escape for drawtext."""
    lines = textwrap.wrap(text, width=width)
    return _escape_ffmpeg_text("\n".join(lines))


async def _generate_scene_clip(
    ffmpeg: str,
    out_path: str,
    colour: str,
    title: str,
    body: str,
    duration: float,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
) -> None:
    """Create a single colour-slide clip. Uses subtitles burn-in for text
    (works even when ffmpeg was built without --enable-libfreetype / drawtext).
    Falls back to plain colour if subtitles filter also missing."""
    # Write an ASS subtitle file for the overlay text
    ass_path = out_path + ".ass"
    _write_ass_subtitle(ass_path, title, body, duration, width, height)

    # Try: colour source + subtitles overlay
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", f"color=c={colour}:s={width}x{height}:d={duration}:r={fps}",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-vf", f"ass={ass_path}",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        out_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    # If ASS filter fails, fall back to plain colour (no text)
    if proc.returncode != 0:
        cmd_fallback = [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", f"color=c={colour}:s={width}x{height}:d={duration}:r={fps}",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            out_path,
        ]
        proc2 = await asyncio.create_subprocess_exec(
            *cmd_fallback, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr2 = await proc2.communicate()
        if proc2.returncode != 0:
            raise RuntimeError(f"FFmpeg scene failed ({proc2.returncode}): {stderr2.decode()[-500:]}")

    # Cleanup temp ASS file
    try:
        os.remove(ass_path)
    except OSError:
        pass


def _write_ass_subtitle(path: str, title: str, body: str, duration: float, w: int, h: int):
    """Write a minimal ASS subtitle file with title + body text."""
    # ASS timestamp format
    def _ts(seconds: float) -> str:
        h_ = int(seconds // 3600)
        m_ = int((seconds % 3600) // 60)
        s_ = seconds % 60
        return f"{h_}:{m_:02d}:{s_:05.2f}"

    end = _ts(duration)
    wrapped_body = "\\N".join(textwrap.wrap(body, width=50))

    content = f"""[Script Info]
Title: Scene
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Arial,56,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,2,8,30,30,{int(h*0.2)},1
Style: Body,Arial,36,&H00CCCCCC,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,8,50,50,{int(h*0.05)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,{end},Title,,0,0,0,,{title}
Dialogue: 0,0:00:00.00,{end},Body,,0,0,0,,{wrapped_body}
"""
    with open(path, "w") as f:
        f.write(content)


async def _concat_clips(ffmpeg: str, clip_paths: list, out_path: str) -> None:
    """Concatenate clips using the FFmpeg concat demuxer."""
    list_file = out_path + ".txt"
    with open(list_file, "w") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0", "-i", list_file,
        "-c:v", "libx264", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-movflags", "+faststart",
        out_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg concat failed ({proc.returncode}): {stderr.decode()[-500:]}")

    # Cleanup temp files
    os.remove(list_file)
    for p in clip_paths:
        try:
            os.remove(p)
        except OSError:
            pass


def _extract_scenes(state: dict) -> list:
    """Pull scene data from storyboard -> script -> planning outputs."""
    # Prefer storyboard shots
    sb = state["outputs"].get("storyboard", {})
    if sb.get("shots"):
        return [
            {
                "title": f"Shot {s.get('scene_id', i+1)}",
                "body": s.get("description", ""),
                "duration": float(s.get("duration_seconds", s.get("duration", 4))),
            }
            for i, s in enumerate(sb["shots"])
        ]

    # Fallback to script scenes
    sc = state["outputs"].get("script", {})
    scenes = sc.get("scenes", sc.get("script_lines", []))
    if scenes:
        return [
            {
                "title": f"Scene {s.get('id', i+1)}",
                "body": s.get("text", s.get("description", "")),
                "duration": float(s.get("duration_seconds", s.get("duration", 5))),
            }
            for i, s in enumerate(scenes)
        ]

    # Last resort: planning
    plan = state["outputs"].get("planning", {})
    plan_scenes = plan.get("scenes", [])
    if plan_scenes:
        return [
            {
                "title": f"Scene {s.get('id', i+1)}",
                "body": s.get("description", str(s)),
                "duration": float(s.get("duration", 5)),
            }
            for i, s in enumerate(plan_scenes)
        ]

    # Absolute fallback
    return [
        {"title": "Director's Cut", "body": state.get("prompt", "Video production"), "duration": 5},
    ]


async def render_node(state: dict) -> dict:
    run_id = state["run_id"]
    stage = Stage.RENDER.value
    await emit_progress(run_id, stage, "Rendering video…")

    # Find FFmpeg
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

    # Prepare output directory
    export_dir = Path("data") / "exports" / run_id
    export_dir.mkdir(parents=True, exist_ok=True)
    final_path = str(export_dir / "render.mp4")

    # Extract scenes from pipeline outputs
    scenes = _extract_scenes(state)
    await think(run_id, stage, f"Rendering {len(scenes)} scenes into video clips…")

    # Generate per-scene clips
    clip_paths = []
    for i, scene in enumerate(scenes):
        colour = SCENE_COLOURS[i % len(SCENE_COLOURS)]
        clip_path = str(export_dir / f"_clip_{i:03d}.mp4")
        await think(run_id, stage, f"Encoding scene {i+1}/{len(scenes)}: {scene['title']}", delay=0.3)
        try:
            await _generate_scene_clip(
                ffmpeg, clip_path, colour,
                scene["title"], scene["body"],
                max(scene["duration"], 2),
            )
            clip_paths.append(clip_path)
        except RuntimeError as e:
            await think(run_id, stage, f"⚠️ Scene {i+1} failed: {str(e)[:100]}")

    if not clip_paths:
        state["outputs"][stage] = {"rendered": False, "error": "All scene renders failed"}
        state["current_stage"] = Stage.PACKAGE.value
        await record_step(run_id, stage, "failed", state["outputs"][stage])
        await checkpoint(state, stage)
        return state

    # Concatenate into final video
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
