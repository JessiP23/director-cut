"""FFmpeg service – deterministic media operations."""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from app.runtime.logger import get_logger

log = get_logger("ffmpeg")


def find_ffmpeg_binary() -> str:
    """Resolve ffmpeg: FFMPEG_PATH env, then app bundle (DIRECTOR_RESOURCES_DIR), then PATH."""
    custom = (os.getenv("FFMPEG_PATH") or "").strip()
    if custom:
        p = Path(custom)
        if p.is_file() and os.access(p, os.X_OK):
            return str(p.resolve())
        w = shutil.which(custom)
        if w:
            return w

    res_root = (os.getenv("DIRECTOR_RESOURCES_DIR") or "").strip()
    if res_root:
        base = Path(res_root)
        names: list[Path] = [
            base / "ffmpeg",
            base / "ffmpeg.exe",
            base / "bin" / "ffmpeg",
            base / "bin" / "ffmpeg.exe",
        ]
        for candidate in names:
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate.resolve())
            except OSError:
                continue

    sys = shutil.which("ffmpeg")
    if sys:
        return sys
    for abs_try in ("/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"):
        if os.path.isfile(abs_try) and os.access(abs_try, os.X_OK):
            return abs_try

    raise FileNotFoundError(
        "FFmpeg not found. Bundle an ffmpeg binary in the app Resources folder (or set FFMPEG_PATH); "
        "for development install ffmpeg (e.g. brew install ffmpeg)."
    )


async def run_ffmpeg(args: list[str], cwd: str | None = None) -> str:
    """Run an FFmpeg command and return stdout."""
    exe = find_ffmpeg_binary()
    cmd = [exe, "-y"] + args
    log.info("ffmpeg_exec", cmd=" ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {stderr.decode()}")
    return stdout.decode()


async def concat_videos(input_paths: list[str], output_path: str):
    """Concatenate a list of video files."""
    list_file = output_path + ".txt"
    with open(list_file, "w") as f:
        for p in input_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")
    await run_ffmpeg(["-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", output_path])
    os.remove(list_file)


async def transcode(input_path: str, output_path: str, preset: str = "medium"):
    """Transcode a video to H.264/AAC."""
    await run_ffmpeg([
        "-i", input_path,
        "-c:v", "libx264", "-preset", preset,
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ])


async def add_audio(video_path: str, audio_path: str, output_path: str):
    """Mux an audio track onto a video."""
    await run_ffmpeg([
        "-i", video_path, "-i", audio_path,
        "-c:v", "copy", "-c:a", "aac",
        "-map", "0:v:0", "-map", "1:a:0",
        output_path,
    ])


async def burn_subtitles(video_path: str, srt_path: str, output_path: str):
    """Burn SRT subtitles into video."""
    await run_ffmpeg([
        "-i", video_path,
        "-vf", f"subtitles={srt_path}",
        "-c:a", "copy",
        output_path,
    ])
