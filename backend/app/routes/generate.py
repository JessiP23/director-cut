"""Image generation endpoint — text-to-image and image-to-image via fal.ai."""
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

# ── fal.ai model selection ────────────────────────────────────────────────────

_TEXT_TO_IMAGE = {
    "draft":    "fal-ai/flux/schnell",   # 4 steps, ~3-5 s
    "standard": "fal-ai/flux/dev",       # 28 steps, ~8-15 s
    "high":     "fal-ai/flux/dev",       # 50 steps, ~15-25 s
}
_IMAGE_TO_IMAGE = "fal-ai/flux/dev/image-to-image"

_QUALITY_STEPS = {"draft": 4, "standard": 28, "high": 50}

# fal.ai image_size presets  →  (width, height hint for display only)
_ASPECT_SIZES: dict[str, str] = {
    "1:1":   "square_hd",
    "16:9":  "landscape_16_9",
    "9:16":  "portrait_16_9",
    "4:3":   "landscape_4_3",
    "3:4":   "portrait_4_3",
    "21:9":  "landscape_16_9",  # closest preset
}

_VALID_ASPECTS = list(_ASPECT_SIZES.keys())
_VALID_QUALITIES = ["draft", "standard", "high"]

_EXPORT_DIR = Path(__file__).resolve().parents[2] / "data" / "exports"


def _image_ext_from_bytes(payload: bytes) -> str | None:
    """Detect common image types from magic bytes (replaces stdlib imghdr, removed in Python 3.13)."""
    if len(payload) >= 3 and payload[0:3] == b"\xff\xd8\xff":
        return "jpeg"
    if len(payload) >= 8 and payload[0:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if len(payload) >= 6 and payload[0:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if len(payload) >= 12 and payload[0:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "webp"
    return None


# ── Request / Response schemas ────────────────────────────────────────────────

class ImageGenRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000,
                        description="Text description of the desired image.")
    reference_image_url: str | None = Field(
        None,
        description="Optional HTTPS URL of an existing image to use as style/content reference.",
    )
    reference_image_base64: str | None = Field(
        None,
        description="Optional base64-encoded image (raw base64 or data URI like "
                    "'data:image/jpeg;base64,...'). Use this when the user uploads a local file. "
                    "Supersedes reference_image_url if both are provided.",
    )
    aspect_ratio: Literal["1:1", "16:9", "9:16", "4:3", "3:4", "21:9"] = Field(
        "16:9",
        description="Output aspect ratio. Options: 1:1, 16:9 (default), 9:16, 4:3, 3:4, 21:9.",
    )
    quality: Literal["draft", "standard", "high"] = Field(
        "standard",
        description="Generation quality. draft=fast/3-5s, standard=balanced/8-15s, high=best/15-25s.",
    )
    strength: float = Field(
        0.8,
        ge=0.1, le=1.0,
        description="For image-to-image: 0.1=very close to reference, 1.0=ignore reference.",
    )


class ImageGenResponse(BaseModel):
    image_url: str
    prompt: str
    aspect_ratio: str
    quality: str
    model: str
    elapsed_seconds: float
    reference_used: bool


# ── Helper ────────────────────────────────────────────────────────────────────

def _fal_key() -> str:
    key = (os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or "").strip()
    if not key:
        raise HTTPException(503, "FAL_KEY not configured on the server.")
    return key


async def _call_fal_image(
    prompt: str,
    image_size: str,
    steps: int,
    model: str,
    fal_key: str,
    reference_image_url: str | None = None,
    strength: float = 0.8,
) -> str:
    """Call fal.ai synchronous image endpoint. Returns the image URL from fal CDN."""
    headers = {
        "Authorization": f"Key {fal_key}",
        "Content-Type": "application/json",
    }

    if reference_image_url:
        url = f"https://fal.run/{_IMAGE_TO_IMAGE}"
        payload: dict = {
            "prompt": prompt,
            "image_url": reference_image_url,
            "image_size": image_size,
            "num_inference_steps": steps,
            "strength": strength,
            "enable_safety_checker": False,
        }
    else:
        url = f"https://fal.run/{model}"
        payload = {
            "prompt": prompt,
            "image_size": image_size,
            "num_inference_steps": steps,
            "num_images": 1,
            "enable_safety_checker": False,
        }

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code in (401, 403):
            raise HTTPException(502, f"fal.ai auth error ({resp.status_code}): check FAL_KEY.")
        if resp.status_code == 422:
            raise HTTPException(422, f"fal.ai rejected request: {resp.text[:300]}")
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


async def _download_image(fal_url: str, out_path: Path) -> None:
    """Download fal CDN image to local exports dir."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.get(fal_url)
        r.raise_for_status()
        out_path.write_bytes(r.content)


def _save_base64_upload(data: str, base_url: str) -> str:
    """Decode a base64 image (raw or data-URI), persist it, return its public URL."""
    raw = data.strip()

    # Strip data-URI prefix: data:image/jpeg;base64,....
    if raw.startswith("data:"):
        header, _, raw = raw.partition(",")
        mime = header.split(";")[0].replace("data:", "").lower()
    else:
        mime = ""

    # Add missing padding (base64 must be a multiple of 4)
    raw += "=" * (-len(raw) % 4)
    try:
        img_bytes = base64.b64decode(raw)
    except Exception as exc:
        raise HTTPException(400, f"Invalid base64 image data: {exc}") from exc

    ext_map = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif"}
    mime_map = {"image/jpeg": ".jpg", "image/png": ".png",
                "image/webp": ".webp", "image/gif": ".gif"}
    detected = _image_ext_from_bytes(img_bytes)
    ext = mime_map.get(mime) or ext_map.get(detected or "", ".jpg")

    upload_id = uuid.uuid4().hex
    out_path = _EXPORT_DIR / "uploads" / f"{upload_id}{ext}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(img_bytes)

    return f"{base_url}/media/exports/uploads/{upload_id}{ext}"


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/image", response_model=ImageGenResponse)
async def generate_image(body: ImageGenRequest, request: Request):
    """Generate an image from a text prompt with optional image reference.

    - **prompt**: Describe what you want.
    - **reference_image_url**: (optional) Base image for style/content transfer.
    - **aspect_ratio**: 16:9 (default), 1:1, 9:16, 4:3, 3:4, 21:9.
    - **quality**: draft (fastest), standard (default), high (best quality).
    - **strength**: How much to deviate from the reference image (image-to-image only).

    Returns a direct HTTPS URL to the generated image.
    """
    fal_key = _fal_key()
    image_size = _ASPECT_SIZES[body.aspect_ratio]
    steps = _QUALITY_STEPS[body.quality]
    model = _TEXT_TO_IMAGE[body.quality]

    # Build base URL for serving uploaded files back to fal.ai
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    base_url = f"{scheme}://{host}"

    # Resolve reference image: base64 upload takes priority over URL
    ref_url: str | None = body.reference_image_url
    if body.reference_image_base64:
        ref_url = _save_base64_upload(body.reference_image_base64, base_url)

    t0 = time.monotonic()

    try:
        fal_url = await _call_fal_image(
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

    # Persist locally so the URL is stable
    gen_id = uuid.uuid4().hex
    ext = ".jpg"
    out_path = _EXPORT_DIR / f"gen_{gen_id}" / f"image{ext}"
    try:
        await _download_image(fal_url, out_path)
    except Exception as e:
        # If download fails, return fal CDN URL directly (may expire)
        import traceback; traceback.print_exc()
        raise HTTPException(502, f"Failed to save image: {e}") from e

    elapsed = round(time.monotonic() - t0, 2)

    image_url = f"{base_url}/media/exports/gen_{gen_id}/image{ext}"

    return ImageGenResponse(
        image_url=image_url,
        prompt=body.prompt,
        aspect_ratio=body.aspect_ratio,
        quality=body.quality,
        model=_IMAGE_TO_IMAGE if body.reference_image_url else model,
        elapsed_seconds=elapsed,
        reference_used=bool(ref_url),
    )
