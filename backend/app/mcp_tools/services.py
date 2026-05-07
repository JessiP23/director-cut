"""Lower-level MCP tools wrapping shared Director services."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from fastmcp import Context, FastMCP

_EXPORT_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "exports"


def _sanitize_export_path(rel: str) -> Path:
    p = Path(rel.strip().lstrip("/"))
    resolved = (_EXPORT_ROOT / p).resolve()
    root_resolved = _EXPORT_ROOT.resolve()
    if root_resolved == resolved:
        raise ValueError("Invalid path")
    if root_resolved not in resolved.parents and resolved != root_resolved:
        raise ValueError("Path escapes exports sandbox")
    if not resolved.is_file():
        raise FileNotFoundError("No such file")
    return resolved


def register_service_tools(mcp: FastMCP) -> None:
    """Attach director.service.* tools to the MCP server."""

    @mcp.tool(name="director.service.llm_call")
    async def director_service_llm_call(
        ctx: Context,
        prompt: str,
        system: str = "",
        model: str = "",
    ) -> dict:
        """Invoke the configured Groq/OpenRouter-backed LLM and return structured output."""
        from app.db.repository import SettingsRepository
        from app.services.llm import call_llm

        await ctx.report_progress(progress=10, total=100, message="Loading settings")
        settings = await SettingsRepository().get_all()
        await ctx.report_progress(progress=40, total=100, message="Calling LLM")
        result = await call_llm(system or "", prompt, model=(model.strip() or None), settings=settings)
        txt = ""
        if isinstance(result, dict):
            if "raw" in result:
                txt = str(result.get("raw", ""))
            else:
                txt = json.dumps(result, ensure_ascii=False, indent=2)[:24000]
        else:
            txt = str(result)
        approx_tokens = max(len(txt.split()) // 2, len(txt) // 6)
        used = (model or "").strip()
        await ctx.report_progress(progress=100, total=100, message="Done")
        return {
            "response_text": txt,
            "model_used": used or "(provider default)",
            "tokens_approx": approx_tokens,
        }

    @mcp.tool(name="director.service.ffmpeg_probe")
    async def director_service_ffmpeg_probe(
        ctx: Context,
        file_path: str,
    ) -> dict:
        """Run ffprobe on a file inside backend/data/exports and return codec metadata."""
        target = await asyncio.to_thread(_sanitize_export_path, file_path)
        await ctx.report_progress(progress=20, total=100, message="Starting ffprobe")
        ffprobe = shutil.which("ffprobe") or "/usr/local/bin/ffprobe"
        proc = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        await ctx.report_progress(progress=90, total=100, message="Parsing")
        if proc.returncode != 0:
            raise RuntimeError((err or b"").decode()[-800:] or f"ffprobe exit {proc.returncode}")
        data = json.loads(out.decode() or "{}")
        vid = None
        for s in data.get("streams") or []:
            if s.get("codec_type") == "video":
                vid = s
                break
        fmt = data.get("format") or {}
        duration = float(fmt.get("duration") or 0.0)
        width = height = codec = fps = None
        if vid:
            width = vid.get("width")
            height = vid.get("height")
            codec = vid.get("codec_name")
            afr = vid.get("avg_frame_rate") or vid.get("r_frame_rate") or ""
            if afr and isinstance(afr, str) and "/" in afr:
                num, denom = afr.split("/", 1)
                try:
                    if float(denom):
                        fps = round(float(num) / float(denom), 4)
                except ValueError:
                    fps = None
        await ctx.report_progress(progress=100, total=100, message="Done")
        return {
            "duration": duration,
            "width": width,
            "height": height,
            "codec": codec or "",
            "fps": fps,
            "filename": target.name,
        }

    @mcp.tool(name="director.service.asset_url")
    async def director_service_asset_url(run_id: str, filename: str) -> dict:
        """Resolve a browser-playable exports URL rooted at /media/exports."""
        fname = Path(filename.strip()).name
        safe_run = "".join(ch for ch in run_id.strip() if ch.isalnum())
        if not safe_run or not fname:
            raise ValueError("run_id and filename required")
        return {
            "url": f"/media/exports/{safe_run}/{fname}",
            "absolute": False,
        }
