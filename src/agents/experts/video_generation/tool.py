"""Provider tools for the video generation expert."""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from google import genai
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models import LlmRequest
from google.genai import types
from google.genai.types import Content, Part

from conf.system import SYS_CONFIG
from src.logger import logger
from src.runtime.workspace import resolve_workspace_path

_SUPPORTED_MODES = {
    "prompt",
    "first_frame",
    "first_frame_and_last_frame",
    "reference_asset",
    "reference_style",
}
_SUPPORTED_ASPECT_RATIOS = {"16:9", "9:16"}
_SUPPORTED_RESOLUTIONS = {"720p", "1080p"}
_SEEDANCE_MODEL_NAME = "doubao-seedance-1-0-pro-250528"
_VEO_MODEL_NAME = "veo-3.0-generate-preview"


@dataclass(slots=True)
class VideoGenerationResult:
    """Normalized result for one provider-specific video generation call."""

    status: str
    message: bytes | str
    provider: str
    model_name: str


def normalize_video_mode(raw_value: str) -> str:
    """Return one supported video generation mode."""
    value = str(raw_value or "").strip().lower()
    return value if value in _SUPPORTED_MODES else "prompt"


def normalize_video_aspect_ratio(raw_value: str) -> str:
    """Return one supported aspect ratio for video generation."""
    value = str(raw_value or "").strip()
    return value if value in _SUPPORTED_ASPECT_RATIOS else "16:9"


def normalize_video_resolution(raw_value: str) -> str:
    """Return one supported output resolution for VEO generation."""
    value = str(raw_value or "").strip()
    return value if value in _SUPPORTED_RESOLUTIONS else "720p"


def _guess_image_mime_type(path: str) -> str:
    """Return the best-effort mime type for one local image file."""
    mime_type, _ = mimetypes.guess_type(path)
    return mime_type or "image/png"


def _read_workspace_image_bytes(path: str) -> bytes:
    """Load one workspace image into memory."""
    return resolve_workspace_path(path).read_bytes()


def _read_workspace_image_as_data_url(path: str) -> str:
    """Load one workspace image and encode it as a data URL."""
    raw_bytes = _read_workspace_image_bytes(path)
    mime_type = _guess_image_mime_type(path)
    encoded = base64.b64encode(raw_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _read_workspace_image_as_genai_image(path: str) -> types.Image:
    """Load one workspace image into a `google.genai.types.Image` object."""
    return types.Image(
        image_bytes=_read_workspace_image_bytes(path),
        mime_type=_guess_image_mime_type(path),
    )


async def prompt_enhancement_tool(ctx: InvocationContext, prompt: str) -> dict[str, str]:
    """Rewrite one video prompt into a more concrete generation prompt."""
    system_prompt = """
    You are a professional prompt optimization expert for text-to-video and image-to-video generation.
    The user will provide a raw video prompt. Improve it while preserving intent.

    Cases:
    1. If the prompt is short or vague, expand it into a more detailed, cinematic, high-quality prompt.
    2. If the prompt is already detailed, preserve the meaning and only improve clarity, sequencing, and visual specificity.

    Output only the optimized prompt text. Do not output JSON or markdown.
    """

    def before_model_callback(
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> None:
        llm_request.contents.append(
            Content(
                role="user",
                parts=[Part(text=f"This is the original prompt: {prompt}\nPlease optimize it.")],
            )
        )

    llm = LlmAgent(
        name="video_prompt_enhancement",
        model=SYS_CONFIG.llm_model,
        instruction=system_prompt,
        include_contents="none",
        before_model_callback=before_model_callback,
    )

    try:
        enhanced_prompt = ""
        async for event in llm.run_async(ctx):
            if not event.content or not event.content.parts:
                continue
            for part in event.content.parts:
                if part.text:
                    enhanced_prompt = part.text

        if enhanced_prompt.strip():
            return {"status": "success", "message": enhanced_prompt.strip()}
        return {"status": "error", "message": "Prompt enhancement returned empty text."}
    except Exception as exc:
        logger.opt(exception=exc).error(
            "video prompt enhancement failed: error_type={} error={!r}",
            type(exc).__name__,
            exc,
        )
        return {"status": "error", "message": f"Prompt enhancement failed: {exc}"}


async def seedance_video_generation_tool(
    prompt: str,
    *,
    input_paths: list[str] | None = None,
    mode: str = "prompt",
    aspect_ratio: str = "16:9",
) -> dict[str, Any]:
    """Generate one video via Seedance."""
    logger.info("calling seedance for video generation ...")
    ark_api_key = os.environ.get("ARK_API_KEY", "").strip()
    if not ark_api_key:
        return {
            "status": "error",
            "message": "ARK_API_KEY is not set.",
            "provider": "seedance",
            "model_name": _SEEDANCE_MODEL_NAME,
        }

    try:
        from volcenginesdkarkruntime import Ark
    except Exception as exc:
        return {
            "status": "error",
            "message": f"seedance SDK unavailable: {exc}",
            "provider": "seedance",
            "model_name": _SEEDANCE_MODEL_NAME,
        }

    current_mode = normalize_video_mode(mode)
    current_paths = input_paths or []
    current_ratio = normalize_video_aspect_ratio(aspect_ratio)
    image_urls = [_read_workspace_image_as_data_url(path) for path in current_paths]

    try:
        client = Ark(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=ark_api_key,
        )
        content: list[dict[str, Any]] = []
        if prompt.strip():
            content.append({"type": "text", "text": prompt})

        if current_mode == "first_frame":
            content.append({"type": "image_url", "image_url": {"url": image_urls[0]}})
        elif current_mode == "first_frame_and_last_frame":
            content.extend(
                [
                    {"type": "image_url", "image_url": {"url": image_urls[0]}, "role": "first_frame"},
                    {"type": "image_url", "image_url": {"url": image_urls[1]}, "role": "last_frame"},
                ]
            )
        elif current_mode in {"reference_asset", "reference_style"}:
            reference_role = "subject" if current_mode == "reference_asset" else "style"
            for image_url in image_urls:
                content.append(
                    {"type": "image_url", "image_url": {"url": image_url}, "role": reference_role}
                )

        create_result = client.content_generation.tasks.create(
            model=_SEEDANCE_MODEL_NAME,
            content=content,
            properties={"ratio": current_ratio},
        )
        task_id = create_result.id

        for _ in range(120):
            task_result = client.content_generation.tasks.get(task_id=task_id)
            status = str(getattr(task_result, "status", "")).strip().lower()
            if status == "succeeded":
                video_url = getattr(getattr(task_result, "content", None), "video_url", "")
                if not video_url:
                    return {
                        "status": "error",
                        "message": "seedance returned success without a video URL.",
                        "provider": "seedance",
                        "model_name": _SEEDANCE_MODEL_NAME,
                    }
                import urllib.request

                with urllib.request.urlopen(video_url) as response:
                    return {
                        "status": "success",
                        "message": response.read(),
                        "provider": "seedance",
                        "model_name": _SEEDANCE_MODEL_NAME,
                    }
            if status == "failed":
                error_obj = getattr(task_result, "error", None)
                return {
                    "status": "error",
                    "message": f"seedance generation failed: {error_obj or 'unknown error'}",
                    "provider": "seedance",
                    "model_name": _SEEDANCE_MODEL_NAME,
                }
            await asyncio.sleep(5)

        return {
            "status": "error",
            "message": "seedance generation timed out while polling task status.",
            "provider": "seedance",
            "model_name": _SEEDANCE_MODEL_NAME,
        }
    except Exception as exc:
        logger.opt(exception=exc).error(
            "seedance video generation failed: error_type={} error={!r}",
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": f"seedance exception: {exc}",
            "provider": "seedance",
            "model_name": _SEEDANCE_MODEL_NAME,
        }


async def veo_video_generation_tool(
    prompt: str,
    *,
    input_paths: list[str] | None = None,
    mode: str = "prompt",
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
) -> dict[str, Any]:
    """Generate one video via Google's VEO API."""
    logger.info("calling veo for video generation ...")
    google_api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not google_api_key:
        return {
            "status": "error",
            "message": "GOOGLE_API_KEY is not set.",
            "provider": "veo",
            "model_name": _VEO_MODEL_NAME,
        }

    current_mode = normalize_video_mode(mode)
    current_paths = input_paths or []
    current_ratio = normalize_video_aspect_ratio(aspect_ratio)
    current_resolution = normalize_video_resolution(resolution)

    try:
        client = genai.Client(api_key=google_api_key)

        source = types.GenerateVideosSource(prompt=prompt or None)
        config_kwargs: dict[str, Any] = {
            "number_of_videos": 1,
            "aspect_ratio": current_ratio,
            "resolution": current_resolution,
        }

        if current_mode == "first_frame":
            source.image = _read_workspace_image_as_genai_image(current_paths[0])
        elif current_mode == "first_frame_and_last_frame":
            source.image = _read_workspace_image_as_genai_image(current_paths[0])
            config_kwargs["last_frame"] = _read_workspace_image_as_genai_image(current_paths[1])
        elif current_mode in {"reference_asset", "reference_style"}:
            ref_type = (
                types.VideoGenerationReferenceType.ASSET
                if current_mode == "reference_asset"
                else types.VideoGenerationReferenceType.STYLE
            )
            config_kwargs["reference_images"] = [
                types.VideoGenerationReferenceImage(
                    image=_read_workspace_image_as_genai_image(path),
                    reference_type=ref_type,
                )
                for path in current_paths
            ]

        operation = await client.aio.models.generate_videos(
            model=_VEO_MODEL_NAME,
            source=source,
            config=types.GenerateVideosConfig(**config_kwargs),
        )

        for _ in range(120):
            if getattr(operation, "done", False):
                break
            await asyncio.sleep(10)
            operation = await client.aio.operations.get(operation)

        if not getattr(operation, "done", False):
            return {
                "status": "error",
                "message": "veo generation timed out while polling operation status.",
                "provider": "veo",
                "model_name": _VEO_MODEL_NAME,
            }

        result = getattr(operation, "result", None)
        generated_videos = getattr(result, "generated_videos", None) or []
        if not generated_videos:
            return {
                "status": "error",
                "message": "veo returned no generated videos.",
                "provider": "veo",
                "model_name": _VEO_MODEL_NAME,
            }

        video = generated_videos[0].video
        video_bytes = await client.aio.files.download(file=video)
        return {
            "status": "success",
            "message": bytes(video_bytes),
            "provider": "veo",
            "model_name": _VEO_MODEL_NAME,
        }
    except Exception as exc:
        logger.opt(exception=exc).error(
            "veo video generation failed: error_type={} error={!r}",
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": f"veo exception: {exc}",
            "provider": "veo",
            "model_name": _VEO_MODEL_NAME,
        }
