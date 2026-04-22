"""Provider tools for the video generation expert."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests
from google import genai
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models import LlmRequest
from google.genai import types
from google.genai.types import Content, Part
from PIL import Image, UnidentifiedImageError

from src.agents.experts.video_generation.capabilities import (
    VIDEO_GENERATION_KLING_MODE_VALUES,
    VIDEO_GENERATION_PERSON_GENERATION_VALUES,
    get_default_video_duration,
    get_default_video_resolution,
    normalize_provider_video_aspect_ratio,
    normalize_provider_video_duration,
    normalize_provider_video_mode,
    normalize_provider_video_resolution,
)
from conf.api import API_CONFIG
from conf.llm import build_llm
from src.logger import logger
from src.runtime.workspace import resolve_workspace_path

_SEEDANCE_MODEL_NAME = "doubao-seedance-1-0-pro-250528"
_VEO_MODEL_NAME = "veo-3.1-generate-preview"
_KLING_MODEL_NAME = "kling-v3"
_KLING_MULTI_REFERENCE_MODEL_NAME = "kling-v1-6"
_DEFAULT_KLING_API_BASE = "https://api-beijing.klingai.com"
_KLING_API_BASE_CANDIDATES = (
    "https://api-beijing.klingai.com",
    "https://api-singapore.klingai.com",
)
_KLING_AUTH_EXPIRE_SECONDS = 30 * 60
_KLING_API_PROBE_TIMEOUT_SECONDS = 8
_KLING_HTTP_RETRY_ATTEMPTS = 3
_KLING_HTTP_RETRY_DELAY_SECONDS = 1.0
_KLING_ALLOWED_IMAGE_FORMATS = {"jpeg", "png"}
_KLING_IMAGE_MAX_BYTES = 10 * 1024 * 1024
_KLING_IMAGE_MIN_DIMENSION = 300
_KLING_IMAGE_MIN_ASPECT_RATIO = 1 / 2.5
_KLING_IMAGE_MAX_ASPECT_RATIO = 2.5
_KLING_OMNI_ONLY_MODEL_NAMES = {
    "kling-v3-omni",
    "kling-video-o1",
    "kling-image-o1",
}
_resolved_kling_api_base: str | None = None


@dataclass(slots=True)
class VideoGenerationResult:
    """Normalized result for one provider-specific video generation call."""

    status: str
    message: bytes | str
    provider: str
    model_name: str


def normalize_video_mode(raw_value: str) -> str:
    """Return one supported video generation mode."""
    return normalize_provider_video_mode("veo", raw_value)


def normalize_video_aspect_ratio(raw_value: str) -> str:
    """Return one supported aspect ratio for video generation."""
    return normalize_provider_video_aspect_ratio("veo", raw_value)


def normalize_video_resolution(raw_value: str) -> str:
    """Return one supported output resolution for VEO generation."""
    return normalize_provider_video_resolution("veo", raw_value)


def normalize_video_duration(raw_value: Any) -> int:
    """Return one supported Veo duration in seconds."""
    return int(normalize_provider_video_duration("veo", raw_value) or get_default_video_duration("veo") or 8)


def normalize_kling_mode(raw_value: Any) -> str:
    """Return one supported Kling quality mode."""
    value = str(raw_value or "").strip().lower()
    return value if value in VIDEO_GENERATION_KLING_MODE_VALUES else "std"


def normalize_kling_aspect_ratio(raw_value: Any) -> str:
    """Return one supported aspect ratio for Kling video generation."""
    return normalize_provider_video_aspect_ratio("kling", raw_value)


def normalize_kling_duration(raw_value: Any, *, mode: str = "prompt") -> int:
    """Return one supported Kling duration in seconds."""
    return int(
        normalize_provider_video_duration("kling", raw_value, mode=mode)
        or get_default_video_duration("kling")
        or 5
    )


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
    if value not in VIDEO_GENERATION_PERSON_GENERATION_VALUES:
        raise ValueError(
            "person_generation must be one of: "
            f"{sorted(VIDEO_GENERATION_PERSON_GENERATION_VALUES)}."
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


def _read_workspace_file_as_base64(path: str) -> str:
    """Load one workspace file and return raw base64 text without a data URL prefix."""
    return base64.b64encode(resolve_workspace_path(path).read_bytes()).decode("utf-8")


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
    if mode == "multi_reference" and not 2 <= current_count <= 4:
        raise ValueError("mode=multi_reference requires between two and four input images.")
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


def _validate_seedance_constraints(*, mode: str) -> None:
    """Validate Seedance-specific mode support before API invocation."""
    if mode == "video_extension":
        raise ValueError("seedance does not support mode=video_extension.")
    if mode == "multi_reference":
        raise ValueError("seedance does not support mode=multi_reference.")


def _validate_kling_constraints(*, mode: str, input_paths: list[str]) -> None:
    """Validate Kling-specific mode support before API invocation."""
    if mode in {"reference_asset", "reference_style", "video_extension"}:
        raise ValueError(
            "kling currently supports only mode=prompt, mode=first_frame, "
            "mode=first_frame_and_last_frame, and mode=multi_reference."
        )
    if mode == "prompt" and input_paths:
        raise ValueError("mode=prompt does not accept input_path or input_paths for kling.")


def _base64url_encode(raw_bytes: bytes) -> str:
    """Encode one byte string with URL-safe base64 and strip padding."""
    return base64.urlsafe_b64encode(raw_bytes).decode("utf-8").rstrip("=")


def _build_kling_auth_token(access_key: str, secret_key: str) -> str:
    """Build one Kling HS256 JWT token from access and secret keys."""
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": access_key,
        "exp": now + _KLING_AUTH_EXPIRE_SECONDS,
        "nbf": now - 5,
    }
    encoded_header = _base64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _base64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")
    signature = hmac.new(secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{_base64url_encode(signature)}"


def _normalize_kling_api_base(raw_value: Any) -> str:
    """Normalize one Kling API base URL by trimming whitespace and slashes."""
    return str(raw_value or "").strip().rstrip("/")


def _build_kling_probe_headers(access_key: str, secret_key: str) -> dict[str, str]:
    """Build minimal headers for probing one Kling API base."""
    return {"Authorization": f"Bearer {_build_kling_auth_token(access_key, secret_key)}"}


def _probe_kling_api_base_sync(*, api_base: str, headers: dict[str, str]) -> bool:
    """Return whether one Kling API base can answer an authenticated probe request."""
    with _build_kling_http_session() as session:
        try:
            response = session.get(
                f"{api_base}/v1/videos/text2video",
                params={"pageNum": 1, "pageSize": 1},
                headers=headers,
                timeout=_KLING_API_PROBE_TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            return False
    if not response.ok:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("code") in {0, "0", 200, "200"}


def _resolve_kling_api_base(
    access_key: str,
    secret_key: str,
    configured_api_base: str = "",
) -> str:
    """Resolve one working Kling API base, probing official regions when needed."""
    global _resolved_kling_api_base

    normalized_configured_api_base = _normalize_kling_api_base(configured_api_base)
    if normalized_configured_api_base:
        return normalized_configured_api_base
    if _resolved_kling_api_base:
        return _resolved_kling_api_base

    probe_headers = _build_kling_probe_headers(access_key, secret_key)
    for candidate_api_base in _KLING_API_BASE_CANDIDATES:
        if _probe_kling_api_base_sync(api_base=candidate_api_base, headers=probe_headers):
            _resolved_kling_api_base = candidate_api_base
            return candidate_api_base

    logger.warning(
        "kling API base probe failed for all official candidates, falling back to {}",
        _DEFAULT_KLING_API_BASE,
    )
    _resolved_kling_api_base = _DEFAULT_KLING_API_BASE
    return _DEFAULT_KLING_API_BASE


def _get_kling_runtime_settings() -> tuple[str, str, str]:
    """Return Kling credentials plus the normalized API base URL."""
    access_key = os.environ.get("KLING_ACCESS_KEY", "").strip() or str(API_CONFIG.KLING_ACCESS_KEY).strip()
    secret_key = os.environ.get("KLING_SECRET_KEY", "").strip() or str(API_CONFIG.KLING_SECRET_KEY).strip()
    configured_api_base = (
        os.environ.get("KLING_API_BASE", "").strip()
        or str(API_CONFIG.KLING_API_BASE).strip()
    )
    if access_key and secret_key:
        api_base = _resolve_kling_api_base(access_key, secret_key, configured_api_base)
    else:
        api_base = _normalize_kling_api_base(configured_api_base) or _DEFAULT_KLING_API_BASE
    return access_key, secret_key, api_base.rstrip("/")


def _build_kling_headers(access_key: str, secret_key: str) -> dict[str, str]:
    """Build request headers for Kling API calls."""
    return {
        "Authorization": f"Bearer {_build_kling_auth_token(access_key, secret_key)}",
        "Content-Type": "application/json",
    }


def _resolve_kling_model_name(raw_value: Any, *, mode: str) -> str:
    """Resolve one Kling model name while enforcing mode-specific schema limits."""
    value = str(raw_value or "").strip()
    if mode == "multi_reference":
        if not value:
            return _KLING_MULTI_REFERENCE_MODEL_NAME
        if value != _KLING_MULTI_REFERENCE_MODEL_NAME:
            raise ValueError(
                "kling mode=multi_reference currently supports only "
                f"model_name={_KLING_MULTI_REFERENCE_MODEL_NAME}."
            )
        return value
    if value in _KLING_OMNI_ONLY_MODEL_NAMES:
        raise ValueError(
            "kling current integration does not support Omni-only model_name="
            f"{value}. Use the built-in basic routes or keep model_name empty."
        )
    return value or _KLING_MODEL_NAME


def _inspect_kling_image(path: str) -> tuple[str, int, int, int]:
    """Return the detected format, file size, width, and height for one Kling input image."""
    resolved = resolve_workspace_path(path)
    file_size = resolved.stat().st_size
    try:
        with Image.open(resolved) as image:
            image_format = str(image.format or "").strip().lower()
            width, height = image.size
    except (FileNotFoundError, UnidentifiedImageError, OSError) as exc:
        raise ValueError(
            f"Kling input image {path} must be a readable .jpg, .jpeg, or .png file."
        ) from exc
    return image_format, file_size, width, height


def _validate_kling_image(
    path: str,
    *,
    field_name: str,
    require_aspect_ratio: bool,
) -> None:
    """Validate one local Kling image against the official documented input constraints."""
    image_format, file_size, width, height = _inspect_kling_image(path)
    if image_format not in _KLING_ALLOWED_IMAGE_FORMATS:
        raise ValueError(
            f"Kling {field_name} image {path} must be .jpg, .jpeg, or .png. "
            "The expert does not auto-convert files."
        )
    if file_size > _KLING_IMAGE_MAX_BYTES:
        raise ValueError(
            f"Kling {field_name} image {path} must be 10MB or smaller. "
            "The expert does not auto-resize inputs."
        )
    if width < _KLING_IMAGE_MIN_DIMENSION or height < _KLING_IMAGE_MIN_DIMENSION:
        raise ValueError(
            f"Kling {field_name} image {path} must be at least 300px on both sides. "
            "The expert does not auto-resize inputs."
        )
    if not require_aspect_ratio:
        return

    aspect_ratio = width / height
    if not (_KLING_IMAGE_MIN_ASPECT_RATIO <= aspect_ratio <= _KLING_IMAGE_MAX_ASPECT_RATIO):
        raise ValueError(
            f"Kling {field_name} image {path} must have aspect ratio between 1:2.5 and 2.5:1. "
            "The expert does not auto-crop or auto-resize inputs."
        )


def _validate_kling_input_images(*, mode: str, input_paths: list[str]) -> None:
    """Validate Kling image-guided inputs without mutating the original assets."""
    if mode == "first_frame":
        _validate_kling_image(
            input_paths[0],
            field_name="image",
            require_aspect_ratio=True,
        )
        return
    if mode == "first_frame_and_last_frame":
        _validate_kling_image(
            input_paths[0],
            field_name="image",
            require_aspect_ratio=True,
        )
        _validate_kling_image(
            input_paths[1],
            field_name="image_tail",
            require_aspect_ratio=False,
        )
        return
    if mode == "multi_reference":
        for index, path in enumerate(input_paths, start=1):
            _validate_kling_image(
                path,
                field_name=f"image_list[{index}]",
                require_aspect_ratio=True,
            )


def _build_kling_http_session() -> requests.Session:
    """Build one Kling HTTP session with deterministic environment behavior."""
    session = requests.Session()
    session.trust_env = False
    return session


def _run_kling_retryable_http_call(
    request_call: Callable[[], requests.Response],
    *,
    action: str,
) -> requests.Response:
    """Run one retryable Kling HTTP read call."""
    for attempt in range(1, _KLING_HTTP_RETRY_ATTEMPTS + 1):
        try:
            return request_call()
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.SSLError,
            requests.exceptions.Timeout,
        ) as exc:
            if attempt >= _KLING_HTTP_RETRY_ATTEMPTS:
                raise
            logger.warning(
                "kling {} failed on attempt {}/{} with {}, retrying ...",
                action,
                attempt,
                _KLING_HTTP_RETRY_ATTEMPTS,
                type(exc).__name__,
            )
            time.sleep(_KLING_HTTP_RETRY_DELAY_SECONDS * attempt)
    raise RuntimeError(f"unreachable retry path for kling {action}")


def _decode_kling_json_response(response: requests.Response) -> dict[str, Any]:
    """Decode one Kling JSON response and surface HTTP errors clearly."""
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        response_text = response.text.strip()
        if response_text:
            raise ValueError(
                f"Kling API HTTP {response.status_code}: {response_text}"
            ) from exc
        raise
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Kling response is not a JSON object.")
    code = payload.get("code")
    if code not in (0, "0", None):
        message = payload.get("message") or payload.get("msg") or "unknown error"
        raise ValueError(f"Kling API error: {message}")
    return payload


def _submit_kling_task_sync(
    *,
    api_base: str,
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Submit one Kling generation request synchronously."""
    with _build_kling_http_session() as session:
        response = session.post(
            f"{api_base}{endpoint}",
            headers=headers,
            json=payload,
            timeout=60,
        )
    return _decode_kling_json_response(response)


def _get_kling_task_sync(
    *,
    api_base: str,
    endpoint: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    """Fetch one Kling task status synchronously."""
    with _build_kling_http_session() as session:
        response = _run_kling_retryable_http_call(
            lambda: session.get(
                f"{api_base}{endpoint}",
                headers=headers,
                timeout=60,
            ),
            action="task polling",
        )
    return _decode_kling_json_response(response)


def _download_binary_sync(url: str) -> bytes:
    """Download one remote binary blob synchronously."""
    with _build_kling_http_session() as session:
        response = _run_kling_retryable_http_call(
            lambda: session.get(url, timeout=120),
            action="binary download",
        )
        response.raise_for_status()
        return response.content


def _extract_kling_task_id(payload: dict[str, Any]) -> str:
    """Extract one Kling task id from a create-task response payload."""
    data = payload.get("data", {})
    task_id = str(data.get("task_id", "")).strip()
    if not task_id:
        raise ValueError("Kling create-task response did not include task_id.")
    return task_id


def _extract_kling_task_status(payload: dict[str, Any]) -> str:
    """Extract one normalized Kling task status from a poll response payload."""
    data = payload.get("data", {})
    return str(data.get("task_status", "")).strip().lower()


def _extract_kling_video_url(payload: dict[str, Any]) -> str:
    """Extract the first returned video URL from a Kling task-result payload."""
    data = payload.get("data", {})
    task_result = data.get("task_result", {}) if isinstance(data, dict) else {}
    videos = task_result.get("videos", []) if isinstance(task_result, dict) else []
    if not isinstance(videos, list) or not videos:
        raise ValueError("Kling task result did not include any videos.")
    first_video = videos[0] if isinstance(videos[0], dict) else {}
    video_url = str(first_video.get("url", "")).strip()
    if not video_url:
        raise ValueError("Kling task result did not include a downloadable video URL.")
    return video_url


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

    current_mode = str(mode or "").strip().lower() or "prompt"
    current_paths = input_paths or []
    current_ratio = normalize_provider_video_aspect_ratio("seedance", aspect_ratio)
    try:
        _validate_seedance_constraints(mode=current_mode)
        _validate_mode_input_paths(current_mode, current_paths)
    except ValueError as exc:
        return {
            "status": "error",
            "message": str(exc),
            "provider": "seedance",
            "model_name": _SEEDANCE_MODEL_NAME,
        }
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

    current_mode = str(mode or "").strip().lower() or "prompt"
    current_paths = input_paths or []
    current_ratio = normalize_provider_video_aspect_ratio("veo", aspect_ratio)
    current_resolution = normalize_provider_video_resolution(
        "veo",
        resolution or get_default_video_resolution("veo"),
    )
    current_duration = int(
        normalize_provider_video_duration(
            "veo",
            duration_seconds,
            mode=current_mode,
        )
        or get_default_video_duration("veo")
        or 8
    )
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


async def kling_video_generation_tool(
    prompt: str,
    *,
    input_paths: list[str] | None = None,
    mode: str = "prompt",
    aspect_ratio: str = "16:9",
    duration_seconds: int = 5,
    negative_prompt: str = "",
    model_name: str = "",
    kling_mode: str = "std",
) -> dict[str, Any]:
    """Generate one video via the Kling official video API."""
    logger.info("calling kling for video generation ...")
    access_key, secret_key, api_base = _get_kling_runtime_settings()
    if not access_key or not secret_key:
        return {
            "status": "error",
            "message": "KLING_ACCESS_KEY or KLING_SECRET_KEY is not set.",
            "provider": "kling",
            "model_name": model_name or _KLING_MODEL_NAME,
        }

    current_mode = str(mode or "").strip().lower() or "prompt"
    current_paths = input_paths or []
    current_ratio = normalize_provider_video_aspect_ratio("kling", aspect_ratio)
    current_duration = int(
        normalize_provider_video_duration(
            "kling",
            duration_seconds,
            mode=current_mode,
        )
        or get_default_video_duration("kling")
        or 5
    )
    current_negative_prompt = str(negative_prompt or "").strip()
    current_kling_mode = normalize_kling_mode(kling_mode)
    current_model_name = (
        str(model_name or "").strip()
        or (_KLING_MULTI_REFERENCE_MODEL_NAME if current_mode == "multi_reference" else _KLING_MODEL_NAME)
    )

    try:
        _validate_kling_constraints(mode=current_mode, input_paths=current_paths)
        if current_mode != "prompt":
            _validate_mode_input_paths(current_mode, current_paths)
        current_model_name = _resolve_kling_model_name(model_name, mode=current_mode)
        if current_mode != "prompt":
            _validate_kling_input_images(mode=current_mode, input_paths=current_paths)

        endpoint = "/v1/videos/text2video"
        payload: dict[str, Any] = {
            "model_name": current_model_name,
            "mode": current_kling_mode,
            "duration": str(current_duration),
            "aspect_ratio": current_ratio,
        }
        if prompt.strip():
            payload["prompt"] = prompt.strip()
        if current_negative_prompt:
            payload["negative_prompt"] = current_negative_prompt

        if current_mode == "first_frame":
            endpoint = "/v1/videos/image2video"
            payload["image"] = _read_workspace_file_as_base64(current_paths[0])
        elif current_mode == "first_frame_and_last_frame":
            endpoint = "/v1/videos/image2video"
            payload["image"] = _read_workspace_file_as_base64(current_paths[0])
            payload["image_tail"] = _read_workspace_file_as_base64(current_paths[1])
        elif current_mode == "multi_reference":
            endpoint = "/v1/videos/multi-image2video"
            payload["image_list"] = [
                {"image": _read_workspace_file_as_base64(path)}
                for path in current_paths
            ]

        headers = _build_kling_headers(access_key, secret_key)
        create_payload = await asyncio.to_thread(
            _submit_kling_task_sync,
            api_base=api_base,
            endpoint=endpoint,
            headers=headers,
            payload=payload,
        )
        task_id = _extract_kling_task_id(create_payload)
        task_endpoint = f"{endpoint}/{task_id}"

        success_statuses = {"succeed", "succeeded", "success", "completed"}
        failure_statuses = {"failed", "error", "canceled", "cancelled"}

        for _ in range(120):
            task_payload = await asyncio.to_thread(
                _get_kling_task_sync,
                api_base=api_base,
                endpoint=task_endpoint,
                headers=headers,
            )
            task_status = _extract_kling_task_status(task_payload)
            if task_status in success_statuses:
                video_url = _extract_kling_video_url(task_payload)
                video_bytes = await asyncio.to_thread(_download_binary_sync, video_url)
                return {
                    "status": "success",
                    "message": video_bytes,
                    "provider": "kling",
                    "model_name": current_model_name,
                }
            if task_status in failure_statuses:
                raise ValueError(f"Kling generation failed with task_status={task_status}.")
            await asyncio.sleep(5)

        return {
            "status": "error",
            "message": "kling generation timed out while polling task status.",
            "provider": "kling",
            "model_name": current_model_name,
        }
    except Exception as exc:
        logger.opt(exception=exc).error(
            "kling video generation failed: error_type={} error={!r}",
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": f"kling exception: {exc}",
            "provider": "kling",
            "model_name": current_model_name,
        }
