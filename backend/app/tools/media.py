"""Thin wrappers that agents can invoke as LangChain-style tools."""
from __future__ import annotations

try:
    from langchain_core.tools import tool
except ImportError:
    # Fallback: define a no-op decorator so module loads without langchain
    def tool(fn):  # type: ignore
        return fn
from app.services.ffmpeg import concat_videos, transcode, add_audio, burn_subtitles
from app.services.llm import call_llm


@tool
async def llm_call(system: str, user: str, model: str = "") -> dict:
    """Call an LLM via OpenRouter."""
    return await call_llm(system=system, user=user, model=model or None)


@tool
async def ffmpeg_concat(input_paths: list[str], output_path: str) -> str:
    """Concatenate video files."""
    await concat_videos(input_paths, output_path)
    return output_path


@tool
async def ffmpeg_transcode(input_path: str, output_path: str, preset: str = "medium") -> str:
    """Transcode a video."""
    await transcode(input_path, output_path, preset)
    return output_path


@tool
async def ffmpeg_add_audio(video_path: str, audio_path: str, output_path: str) -> str:
    """Mux audio onto video."""
    await add_audio(video_path, audio_path, output_path)
    return output_path


@tool
async def ffmpeg_burn_subs(video_path: str, srt_path: str, output_path: str) -> str:
    """Burn subtitles into video."""
    await burn_subtitles(video_path, srt_path, output_path)
    return output_path
