"""Provider tools for the video generation expert."""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
from dataclasses import dataclass
from typing import Any

from google import genai
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models import LlmRequest
from google.genai import types
from google.genai.types import Content, Part

from conf.llm import build_llm
from src.logger import logger
from src.runtime.workspace import resolve_workspace_path

_SUPPORTED_MODES = {
    "prompt",
    "first_frame",
    "first_frame_and_last_frame",
    "reference_asset",
    "reference_style",
    "video_extension",
}
_SUPPORTED_ASPECT_RATIOS = {"16:9", "9:16"}
_SUPPORTED_RESOLUTIONS = {"720p", "1080p", "4k"}
_SUPPORTED_VEO_DURATIONS = {4, 6, 8}
_SUPPORTED_PERSON_GENERATION = {"allow_all", "allow_adult"}
_SEEDANCE_MODEL_NAME = "doubao-seedance-1-0-pro-250528"
_VEO_MODEL_NAME = "veo-3.1-generate-preview"


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
    value = str(raw_value or "").strip().lower()
    return value if value in _SUPPORTED_RESOLUTIONS else "720p"


def normalize_video_duration(raw_value: Any) -> int:
    """Return one supported Veo duration in seconds."""
    if raw_value is None or str(raw_value).strip() == "":
        return 8
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        return 8
    return value if value in _SUPPORTED_VEO_DURATIONS else 8


def normalize_optional_boolean(raw_value: Any, *, parameter_name: str) -> bool | None:
    """Parse one optional boolean-like value."""
    if raw_value is None:
        return None
    if isinstance(raw_value, bool):
        return raw_value
    normalized = str(raw_value).strip().lower()
    if not normalized:
        return None
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{parameter_name} must be a boolean value.")


def normalize_video_seed(raw_value: Any) -> int | None:
    """Parse one optional Veo seed value."""
    if raw_value is None or str(raw_value).strip() == "":
        return None
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("seed must be an integer.") from exc
    if value < 0 or value > 4_294_967_295:
        raise ValueError("seed must be between 0 and 4294967295.")
    return value


def normalize_person_generation(raw_value: Any) -> str | None:
    """Return one supported Veo person generation value when provided."""
    if raw_value is None:
        return None
    value = str(raw_value).strip().lower()
    if not value:
        return None
    if value not in _SUPPORTED_PERSON_GENERATION:
        raise ValueError(
            "person_generation must be one of: "
            f"{sorted(_SUPPORTED_PERSON_GENERATION)}."
        )
    return value


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


def _read_workspace_video_as_genai_video(path: str) -> types.Video:
    """Load one workspace video into a `google.genai.types.Video` object."""
    return types.Video.from_file(location=str(resolve_workspace_path(path)))


def _validate_mode_input_paths(mode: str, input_paths: list[str]) -> None:
    """Validate mode-specific input count constraints before provider calls."""
    current_count = len(input_paths)
    if mode == "first_frame" and current_count != 1:
        raise ValueError("mode=first_frame requires exactly one input image.")
    if mode == "first_frame_and_last_frame" and current_count != 2:
        raise ValueError("mode=first_frame_and_last_frame requires exactly two input images.")
    if mode in {"reference_asset", "reference_style"} and not 1 <= current_count <= 3:
        raise ValueError(f"mode={mode} requires between one and three input images.")
    if mode == "video_extension" and current_count != 1:
        raise ValueError("mode=video_extension requires exactly one input video.")


def _validate_veo_constraints(
    *,
    mode: str,
    resolution: str,
    duration_seconds: int,
    person_generation: str | None,
) -> None:
    """Validate Veo-specific parameter combinations before API invocation."""
    if resolution in {"1080p", "4k"} and duration_seconds != 8:
        raise ValueError(f"resolution={resolution} requires duration_seconds=8 for Veo.")
    if mode in {"reference_asset", "reference_style"} and duration_seconds != 8:
        raise ValueError(f"mode={mode} requires duration_seconds=8 for Veo.")
    if mode == "video_extension":
        if duration_seconds != 8:
            raise ValueError("mode=video_extension requires duration_seconds=8 for Veo.")
        if resolution != "720p":
            raise ValueError("mode=video_extension only supports resolution=720p for Veo.")
    if mode in {"first_frame", "first_frame_and_last_frame", "reference_asset", "reference_style"}:
        if person_generation == "allow_all":
            raise ValueError(
                f"mode={mode} only supports person_generation=allow_adult for Veo."
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
        model=build_llm(),
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
    if current_mode == "video_extension":
        return {
            "status": "error",
            "message": "seedance does not support mode=video_extension.",
            "provider": "seedance",
            "model_name": _SEEDANCE_MODEL_NAME,
        }
    _validate_mode_input_paths(current_mode, current_paths)
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
            ratio=current_ratio,
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
    duration_seconds: int = 8,
    negative_prompt: str = "",
    person_generation: str | None = None,
    seed: int | None = None,
    enhance_prompt: bool | None = None,
) -> dict[str, Any]:
    """Generate one video via Google's VEO API."""
    logger.info("calling veo for video generation ...")
    google_api_key = (
        os.environ.get("GOOGLE_API_KEY", "").strip()
        or os.environ.get("GEMINI_API_KEY", "").strip()
    )
    if not google_api_key:
        return {
            "status": "error",
            "message": "GOOGLE_API_KEY or GEMINI_API_KEY is not set.",
            "provider": "veo",
            "model_name": _VEO_MODEL_NAME,
        }

    current_mode = normalize_video_mode(mode)
    current_paths = input_paths or []
    current_ratio = normalize_video_aspect_ratio(aspect_ratio)
    current_resolution = normalize_video_resolution(resolution)
    current_duration = normalize_video_duration(duration_seconds)
    current_negative_prompt = str(negative_prompt or "").strip()

    try:
        current_enhance_prompt = normalize_optional_boolean(
            enhance_prompt,
            parameter_name="enhance_prompt",
        )
        current_person_generation = normalize_person_generation(person_generation)
        current_seed = normalize_video_seed(seed)
        _validate_mode_input_paths(current_mode, current_paths)
        _validate_veo_constraints(
            mode=current_mode,
            resolution=current_resolution,
            duration_seconds=current_duration,
            person_generation=current_person_generation,
        )

        client = genai.Client(api_key=google_api_key)

        source = types.GenerateVideosSource(prompt=prompt or None)
        config_kwargs: dict[str, Any] = {
            "number_of_videos": 1,
            "aspect_ratio": current_ratio,
            "resolution": current_resolution,
            "duration_seconds": current_duration,
        }
        if current_negative_prompt:
            config_kwargs["negative_prompt"] = current_negative_prompt
        if current_person_generation:
            config_kwargs["person_generation"] = current_person_generation
        if current_seed is not None:
            config_kwargs["seed"] = current_seed
        if current_enhance_prompt is not None:
            config_kwargs["enhance_prompt"] = current_enhance_prompt

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
        elif current_mode == "video_extension":
            source.video = _read_workspace_video_as_genai_video(current_paths[0])

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
