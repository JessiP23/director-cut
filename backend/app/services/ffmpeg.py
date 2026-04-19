"""FFmpeg service – deterministic media operations."""
from __future__ import annotations

import asyncio
import os
from typing import List, Optional
from app.runtime.logger import get_logger

log = get_logger("ffmpeg")


async def run_ffmpeg(args: list[str], cwd: str | None = None) -> str:
    """Run an FFmpeg command and return stdout."""
    cmd = ["ffmpeg", "-y"] + args
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
