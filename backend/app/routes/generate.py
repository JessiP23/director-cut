from __future__ import annotations
import base64
import os
import time
import uuid
from pathlib import Path
from typing import Literal
import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()

# ── fal.ai endpoints ──────────────────────────────────────────────────────────

_TEXT_TO_IMAGE = {
    "draft":    "fal-ai/flux/schnell",   # 4 steps,  ~3-5 s
    "standard": "fal-ai/flux/dev",       # 28 steps, ~8-15 s
    "high":     "fal-ai/flux/dev",       # 50 steps, ~15-25 s
}
_IMAGE_TO_IMAGE = "fal-ai/flux/dev/image-to-image"

_QUALITY_STEPS = {"draft": 4, "standard": 28, "high": 50}

_ASPECT_SIZES: dict[str, str] = {
    "1:1":  "square_hd",
    "16:9": "landscape_16_9",
    "9:16": "portrait_16_9",
    "4:3":  "landscape_4_3",
    "3:4":  "portrait_4_3",
    "21:9": "landscape_16_9",
}

_EXPORT_DIR  = Path(__file__).resolve().parents[2] / "data" / "exports"
_UPLOAD_DIR  = Path(__file__).resolve().parents[2] / "data" / "uploads"

def _mime_from_bytes(data: bytes) -> tuple[str, str]:
    """Return (mime_type, file_extension) from magic bytes."""
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", ".jpg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", ".png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    if len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", ".gif"
    return "image/jpeg", ".jpg"   # safe default


class ImageGenRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000,
                        description="Text description of the desired image.")
    reference_image_url: str | None = Field(
        None,
        description="HTTPS URL of a reference image for style/content transfer. "
                    "Must be publicly accessible (e.g. from https://director-cut.fly.dev/media/uploads/).",
    )
    reference_image_base64: str | None = Field(
        None,
        description="Base64-encoded image (raw or data-URI). Supersedes reference_image_url.",
    )
    aspect_ratio: Literal["1:1", "16:9", "9:16", "4:3", "3:4", "21:9"] = Field(
        "16:9", description="Output aspect ratio. Default 16:9.",
    )
    quality: Literal["draft", "standard", "high"] = Field(
        "standard", description="draft=~3-5s, standard=~8-15s (default), high=~15-25s.",
    )
    strength: float = Field(
        0.8, ge=0.1, le=1.0,
        description="Image-to-image: 0.1=stay close to reference, 1.0=ignore it.",
    )

class ImageGenResponse(BaseModel):
    image_url: str
    prompt: str
    aspect_ratio: str
    quality: str
    model: str
    elapsed_seconds: float
    reference_used: bool

class ImageUploadRequest(BaseModel):
    image_base64: str = Field(
        ..., description="Base64-encoded image (raw or data-URI).",
    )

class ImageUploadResponse(BaseModel):
    upload_url: str   # public URL served from /media/uploads/ — pass to reference_image_url
    fal_url: str      # same URL (kept for backwards compat with mcp-director client)


def _fal_key() -> str:
    key = (os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or "").strip()
    if not key:
        raise HTTPException(503, "FAL_KEY not configured on the server.")
    return key

def _decode_base64_image(data: str) -> tuple[bytes, str, str]:
    """Accept raw base64 or data-URI. Returns (bytes, mime, ext)."""
    raw = data.strip()
    declared_mime = ""

    if raw.startswith("data:"):
        header, _, raw = raw.partition(",")
        declared_mime = header.split(";")[0].replace("data:", "").lower()

    raw += "=" * (-len(raw) % 4)   # fix missing padding
    try:
        img_bytes = base64.b64decode(raw)
    except Exception as exc:
        raise HTTPException(400, f"Invalid base64 image: {exc}") from exc

    mime, ext = _mime_from_bytes(img_bytes)
    # Honour declared MIME if magic bytes fell back to default
    overrides = {
        "image/jpeg": (".jpg",  "image/jpeg"),
        "image/png":  (".png",  "image/png"),
        "image/webp": (".webp", "image/webp"),
        "image/gif":  (".gif",  "image/gif"),
    }
    if declared_mime in overrides:
        ext, mime = overrides[declared_mime]

    return img_bytes, mime, ext

def _save_upload(img_bytes: bytes) -> tuple[str, str]:
    """Persist image bytes to /data/uploads/, return (upload_id, ext)."""
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    mime, ext = _mime_from_bytes(img_bytes)
    upload_id = uuid.uuid4().hex
    (_UPLOAD_DIR / f"{upload_id}{ext}").write_bytes(img_bytes)
    return upload_id, ext

def _base_url(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    return f"{scheme}://{host}"

async def _call_fal_image(
    prompt: str,
    image_size: str,
    steps: int,
    model: str,
    fal_key: str,
    reference_image_url: str | None = None,
    strength: float = 0.8,
) -> str:
    """Call fal.ai synchronous image endpoint. Returns fal CDN URL of result."""
    headers = {
        "Authorization": f"Key {fal_key}",
        "Content-Type": "application/json",
    }

    if reference_image_url:
        endpoint = f"https://fal.run/{_IMAGE_TO_IMAGE}"
        payload: dict = {
            "prompt": prompt,
            "image_url": reference_image_url,
            "image_size": image_size,
            "num_inference_steps": steps,
            "strength": strength,
            "enable_safety_checker": False,
        }
    else:
        endpoint = f"https://fal.run/{model}"
        payload = {
            "prompt": prompt,
            "image_size": image_size,
            "num_inference_steps": steps,
            "num_images": 1,
            "enable_safety_checker": False,
        }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(endpoint, json=payload, headers=headers)
        if resp.status_code in (401, 403):
            raise HTTPException(502, f"fal.ai auth error ({resp.status_code}): check FAL_KEY.")
        if resp.status_code == 422:
            body_text = resp.text[:600]
            # fal can't fetch our reference URL — surface a structured upload_required error
            if reference_image_url and "download" in body_text.lower():
                raise HTTPException(
                    422,
                    {
                        "upload_required": True,
                        "upload_url": "https://director-cut.fly.dev/upload",
                        "message": (
                            "fal.ai could not download the reference image. "
                            "Please upload your image at https://director-cut.fly.dev/upload "
                            "to get a stable URL, then paste it back here."
                        ),
                        "fal_error": body_text,
                    },
                )
            raise HTTPException(422, f"fal.ai rejected the request: {body_text}")
        resp.raise_for_status()
        data = resp.json()

    images = data.get("images") or data.get("image") or []
    if isinstance(images, dict):
        images = [images]
    if not images:
        raise HTTPException(502, f"fal.ai returned no images. Keys: {list(data.keys())}")

    img_url = images[0].get("url") or images[0].get("image_url")
    if not img_url:
        raise HTTPException(502, f"fal.ai image object missing URL: {images[0]}")
    return str(img_url)

async def _download_and_persist(fal_url: str) -> str:
    """Download fal CDN result to stable local path. Returns gen_id."""
    gen_id = uuid.uuid4().hex
    out_dir = _EXPORT_DIR / f"gen_{gen_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.get(fal_url)
        r.raise_for_status()
        (out_dir / "image.jpg").write_bytes(r.content)
    return gen_id

@router.post("/image", response_model=ImageGenResponse)
async def generate_image(body: ImageGenRequest, request: Request):
    """Generate an image from a text prompt, with optional reference image."""
    fal_key = _fal_key()
    image_size = _ASPECT_SIZES[body.aspect_ratio]
    steps = _QUALITY_STEPS[body.quality]
    model = _TEXT_TO_IMAGE[body.quality]
    base = _base_url(request)
    t0 = time.monotonic()

    ref_url: str | None = None

    if body.reference_image_base64:
        img_bytes, _, _ = _decode_base64_image(body.reference_image_base64)
        upload_id, ext = _save_upload(img_bytes)
        ref_url = f"{base}/media/uploads/{upload_id}{ext}"
    elif body.reference_image_url:
        ref_url = body.reference_image_url

    try:
        fal_result_url = await _call_fal_image(
            prompt=body.prompt,
            image_size=image_size,
            steps=steps,
            model=model,
            fal_key=fal_key,
            reference_image_url=ref_url,
            strength=body.strength,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Image generation failed: {e}") from e

    try:
        gen_id = await _download_and_persist(fal_result_url)
    except Exception as e:
        raise HTTPException(502, f"Failed to save generated image: {e}") from e

    elapsed = round(time.monotonic() - t0, 2)
    image_url = f"{base}/media/exports/gen_{gen_id}/image.jpg"

    return ImageGenResponse(
        image_url=image_url,
        prompt=body.prompt,
        aspect_ratio=body.aspect_ratio,
        quality=body.quality,
        model=_IMAGE_TO_IMAGE if ref_url else model,
        elapsed_seconds=elapsed,
        reference_used=bool(ref_url),
    )

async def _handle_upload(img_bytes: bytes, request: Request) -> ImageUploadResponse:
    """Save bytes to persistent volume, return public URL."""
    upload_id, ext = _save_upload(img_bytes)
    base = _base_url(request)
    url = f"{base}/media/uploads/{upload_id}{ext}"
    return ImageUploadResponse(upload_url=url, fal_url=url)

@router.post("/upload", response_model=ImageUploadResponse)
async def upload_reference_image(request: Request):
    """Pre-upload a reference image. No auth required.

    Accepts EITHER:
      • multipart/form-data with a 'file' field (from the /upload web page)
      • application/json with {"image_base64": "data:image/...;base64,..."}

    Returns upload_url — a stable public URL to pass as reference_image_url
    in director_image_generate.
    """
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        file_field = form.get("file")
        if file_field is None:
            raise HTTPException(400, "Missing 'file' field in multipart form.")
        img_bytes = await file_field.read()
        if not img_bytes:
            raise HTTPException(400, "Uploaded file is empty.")
    else:
        try:
            body_json = await request.json()
        except Exception:
            raise HTTPException(400, "Expected multipart/form-data or JSON {image_base64: ...}")
        b64 = (body_json or {}).get("image_base64", "")
        if not b64:
            raise HTTPException(400, "Missing 'image_base64' field in JSON body.")
        img_bytes, _, _ = _decode_base64_image(b64)

    return await _handle_upload(img_bytes, request)