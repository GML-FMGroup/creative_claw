"""Provider tools for the 3D generation expert."""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from conf.app_config import load_app_config
from src.logger import logger
from src.runtime.workspace import generated_session_dir, resolve_workspace_path

DEFAULT_REGION = "ap-guangzhou"
DEFAULT_MODEL = "3.0"
DEFAULT_GENERATE_TYPE = "Normal"
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_INTERVAL_SECONDS = 8
TERMINAL_STATUSES = {"DONE", "FAIL"}
ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_SEED3D_MODEL = "doubao-seed3d-2-0-260328"
DEFAULT_HYPER3D_MODEL = "hyper3d-gen2-260112"
DEFAULT_HITEM3D_MODEL = "hitem3d-2-0-251223"
DEFAULT_SEED3D_FILE_FORMAT = "glb"
DEFAULT_HYPER3D_FILE_FORMAT = "glb"
DEFAULT_HITEM3D_FILE_FORMAT = "obj"
DEFAULT_SEED3D_SUBDIVISION_LEVEL = "medium"
DEFAULT_HYPER3D_SUBDIVISION_LEVEL = "medium"
DEFAULT_HITEM3D_RESOLUTION = "1536"
DEFAULT_SEED3D_INTERVAL_SECONDS = 60
DEFAULT_HYPER3D_INTERVAL_SECONDS = 60
DEFAULT_HITEM3D_INTERVAL_SECONDS = 60
ARK_3D_TERMINAL_STATUSES = {"succeeded", "failed"}
SEED3D_TERMINAL_STATUSES = ARK_3D_TERMINAL_STATUSES

_GENERATE_TYPE_MAP = {
    "normal": "Normal",
    "lowpoly": "LowPoly",
    "sketch": "Sketch",
    "geometry": "Geometry",
}
_RESULT_FORMAT_MAP = {
    "stl": "STL",
    "usdz": "USDZ",
    "fbx": "FBX",
}
_SEED3D_FILE_FORMATS = {"glb", "obj", "usd", "usdz"}
_HYPER3D_FILE_FORMATS = {"glb", "obj", "usdz", "fbx", "stl"}
_HITEM3D_FILE_FORMATS = {"obj", "glb", "stl", "fbx", "usdz"}
_HITEM3D_FILE_FORMAT_TO_CODE = {
    "obj": 1,
    "glb": 2,
    "stl": 3,
    "fbx": 4,
    "usdz": 5,
}
_HITEM3D_CODE_TO_FILE_FORMAT = {
    str(code): file_format for file_format, code in _HITEM3D_FILE_FORMAT_TO_CODE.items()
}
_SEED3D_SUBDIVISION_LEVELS = {"low", "medium", "high"}
_HYPER3D_SUBDIVISION_LEVELS = {"low", "medium", "high"}
_HYPER3D_MATERIALS = {
    "pbr": "PBR",
    "shaded": "Shaded",
    "all": "All",
    "none": "None",
}
_HYPER3D_MESH_MODES = {
    "raw": "Raw",
    "quad": "Quad",
}
_HYPER3D_ADDONS = {"highpack": "HighPack"}
_HITEM3D_RESOLUTIONS = {"1536": "1536", "1536pro": "1536pro", "1536 pro": "1536pro"}
_ARK_3D_DOWNLOAD_SUFFIXES = {".glb", ".obj", ".usd", ".usdz", ".stl", ".fbx", ".zip"}
_ARK_3D_URL_EXCLUDE_MARKERS = {"image", "preview", "thumbnail", "cover", "video"}
_ARK_3D_URL_INCLUDE_MARKERS = {"asset", "download", "file", "mesh", "model", "result", "url"}


def normalize_generate_type(raw_value: str | None) -> str:
    """Return one supported hy3d generate type."""
    normalized = str(raw_value or "").strip().lower()
    return _GENERATE_TYPE_MAP.get(normalized, DEFAULT_GENERATE_TYPE)


def normalize_result_format(raw_value: str | None) -> str | None:
    """Return one supported hy3d result format or `None`."""
    normalized = str(raw_value or "").strip().lower()
    if not normalized:
        return None
    return _RESULT_FORMAT_MAP.get(normalized)


def normalize_seed3d_file_format(raw_value: Any) -> str:
    """Return one supported Seed3D output file format."""
    normalized = str(raw_value or "").strip().lower()
    return normalized if normalized in _SEED3D_FILE_FORMATS else DEFAULT_SEED3D_FILE_FORMAT


def normalize_seed3d_subdivision_level(raw_value: Any) -> str:
    """Return one supported Seed3D subdivision level."""
    normalized = str(raw_value or "").strip().lower()
    return normalized if normalized in _SEED3D_SUBDIVISION_LEVELS else DEFAULT_SEED3D_SUBDIVISION_LEVEL


def normalize_hyper3d_file_format(raw_value: Any) -> str:
    """Return one supported Hyper3D output file format."""
    normalized = str(raw_value or "").strip().lower()
    return normalized if normalized in _HYPER3D_FILE_FORMATS else DEFAULT_HYPER3D_FILE_FORMAT


def normalize_hyper3d_subdivision_level(raw_value: Any) -> str:
    """Return one supported Hyper3D subdivision level."""
    normalized = str(raw_value or "").strip().lower()
    return normalized if normalized in _HYPER3D_SUBDIVISION_LEVELS else DEFAULT_HYPER3D_SUBDIVISION_LEVEL


def normalize_hitem3d_file_format(raw_value: Any) -> str:
    """Return one supported Hitem3D output file format."""
    normalized = str(raw_value or "").strip().lower()
    if normalized in _HITEM3D_CODE_TO_FILE_FORMAT:
        return _HITEM3D_CODE_TO_FILE_FORMAT[normalized]
    return normalized if normalized in _HITEM3D_FILE_FORMATS else DEFAULT_HITEM3D_FILE_FORMAT


def normalize_hitem3d_resolution(raw_value: Any) -> str:
    """Return one supported Hitem3D resolution value."""
    normalized = str(raw_value or "").strip().lower().replace("_", "").replace("-", "")
    return _HITEM3D_RESOLUTIONS.get(normalized, DEFAULT_HITEM3D_RESOLUTION)


def _build_seed3d_parameter_text(*, subdivision_level: str, file_format: str) -> str:
    """Build the Seed3D command-style parameter text used by Ark tasks."""
    current_level = normalize_seed3d_subdivision_level(subdivision_level)
    current_format = normalize_seed3d_file_format(file_format)
    return f"--subdivisionlevel {current_level} --fileformat {current_format}"


def _normalize_optional_int(
    raw_value: Any,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    name: str,
) -> int | None:
    """Normalize an optional integer and validate its inclusive range."""
    if raw_value in (None, ""):
        return None
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"`{name}` must be an integer.") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"`{name}` must be >= {minimum}.")
    if maximum is not None and value > maximum:
        raise ValueError(f"`{name}` must be <= {maximum}.")
    return value


def _normalize_optional_choice(
    raw_value: Any,
    mapping: dict[str, str],
    *,
    name: str,
) -> str | None:
    """Normalize an optional string value using a supported choices mapping."""
    if raw_value in (None, ""):
        return None
    normalized = str(raw_value).strip().lower()
    if normalized not in mapping:
        allowed = ", ".join(mapping.values())
        raise ValueError(f"`{name}` must be one of: {allowed}.")
    return mapping[normalized]


def _normalize_bbox_condition(raw_value: Any) -> list[int] | None:
    """Normalize the Hyper3D bbox_condition command into three integers."""
    if raw_value in (None, ""):
        return None
    if isinstance(raw_value, str):
        cleaned = raw_value.strip().strip("[]")
        values = [part.strip() for part in cleaned.split(",") if part.strip()]
    elif isinstance(raw_value, (list, tuple)):
        values = list(raw_value)
    else:
        raise ValueError("`bbox_condition` must be a list of three integers.")

    if len(values) != 3:
        raise ValueError("`bbox_condition` must contain exactly three integers.")
    try:
        return [int(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise ValueError("`bbox_condition` must contain only integers.") from exc


def _format_ark_command_value(value: Any) -> str:
    """Format one value for a Volcengine command-style text parameter."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(str(item) for item in value) + "]"
    return str(value)


def _build_ark_command_text(prompt: str | None, commands: list[tuple[str, Any]]) -> str:
    """Build one command-style text item with an optional prompt prefix."""
    parts: list[str] = []
    normalized_prompt = str(prompt or "").strip()
    if normalized_prompt:
        if len(normalized_prompt) > 400:
            raise ValueError("`prompt` must be no longer than 400 characters for Hyper3D.")
        parts.append(normalized_prompt)

    for name, value in commands:
        if value in (None, ""):
            continue
        parts.append(f"--{name} {_format_ark_command_value(value)}")

    return " ".join(parts).strip()


def _build_hyper3d_parameter_text(
    *,
    prompt: str | None,
    file_format: str,
    subdivision_level: str | None = None,
    material: str | None = None,
    mesh_mode: str | None = None,
    quality_override: int | None = None,
    addons: str | None = None,
    use_original_alpha: bool | None = None,
    bbox_condition: Any = None,
    ta_pose: bool | None = None,
    hd_texture: bool | None = None,
) -> str:
    """Build the Hyper3D command-style text used by Ark tasks."""
    current_file_format = normalize_hyper3d_file_format(file_format)
    current_subdivision_level = (
        normalize_hyper3d_subdivision_level(subdivision_level)
        if subdivision_level not in (None, "")
        else None
    )
    current_material = _normalize_optional_choice(
        material,
        _HYPER3D_MATERIALS,
        name="material",
    )
    current_mesh_mode = _normalize_optional_choice(
        mesh_mode,
        _HYPER3D_MESH_MODES,
        name="mesh_mode",
    )
    quality_minimum = 500 if current_mesh_mode == "Raw" else 1000
    quality_maximum = 1000000 if current_mesh_mode == "Raw" else 200000
    current_quality_override = _normalize_optional_int(
        quality_override,
        minimum=quality_minimum,
        maximum=quality_maximum,
        name="quality_override",
    )
    current_addons = _normalize_optional_choice(
        addons,
        _HYPER3D_ADDONS,
        name="addons",
    )
    normalized_bbox = _normalize_bbox_condition(bbox_condition)

    return _build_ark_command_text(
        prompt,
        [
            ("mesh_mode", current_mesh_mode),
            ("hd_texture", hd_texture),
            ("material", current_material),
            ("addons", current_addons),
            ("quality_override", current_quality_override),
            ("use_original_alpha", use_original_alpha),
            ("bbox_condition", normalized_bbox),
            ("TAPose", ta_pose),
            ("subdivisionlevel", current_subdivision_level),
            ("fileformat", current_file_format),
        ],
    )


def _build_hitem3d_parameter_text(
    *,
    file_format: str,
    resolution: str = DEFAULT_HITEM3D_RESOLUTION,
    face_count: int | None = None,
    request_type: int | None = None,
    multi_images_bit: str | None = None,
    image_count: int,
) -> str:
    """Build the Hitem3D command-style text used by Ark tasks."""
    current_file_format = normalize_hitem3d_file_format(file_format)
    current_resolution = normalize_hitem3d_resolution(resolution)
    current_face_count = _normalize_optional_int(
        face_count,
        minimum=100000,
        maximum=2000000,
        name="face",
    )
    current_request_type = _normalize_optional_int(
        request_type,
        minimum=1,
        maximum=3,
        name="request_type",
    )
    if current_request_type not in (None, 1, 3):
        raise ValueError("`request_type` must be 1 or 3.")

    normalized_multi_images_bit = str(multi_images_bit or "").strip()
    if normalized_multi_images_bit:
        if len(normalized_multi_images_bit) != 4 or set(normalized_multi_images_bit) - {"0", "1"}:
            raise ValueError("`multi_images_bit` must be a four-character bit string.")
        if normalized_multi_images_bit.count("1") != image_count:
            raise ValueError("`multi_images_bit` must contain one `1` per input image.")

    return _build_ark_command_text(
        None,
        [
            ("resolution", current_resolution),
            ("request_type", current_request_type),
            ("ff", _HITEM3D_FILE_FORMAT_TO_CODE[current_file_format]),
            ("face", current_face_count),
            ("multi_images_bit", normalized_multi_images_bit or None),
        ],
    )


def coerce_bool(raw_value: Any, default: bool = False) -> bool:
    """Convert one common scalar value into a boolean."""
    if raw_value is None:
        return default
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)):
        return bool(raw_value)

    normalized = str(raw_value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {raw_value}")


def _safe_segment(value: str) -> str:
    """Sanitize one filesystem path segment."""
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)
    return cleaned or "default"


def _build_download_dir(*, session_id: str, turn_index: int, step: int, job_id: str) -> Path:
    """Build the target download directory for one hy3d job."""
    output_dir = (
        generated_session_dir(session_id, turn_index=turn_index)
        / f"turn{turn_index}_step{step}_3d_generation_{_safe_segment(job_id)}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _load_tencentcloud_sdk() -> tuple[Any, Any, Any]:
    """Import the Tencent Cloud AI3D SDK lazily."""
    try:
        from tencentcloud.ai3d.v20250513 import ai3d_client, models
        from tencentcloud.common import credential
        from tencentcloud.common.exception.tencent_cloud_sdk_exception import (
            TencentCloudSDKException,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Tencent Cloud SDK is not available. Install `tencentcloud-sdk-python`."
        ) from exc

    return ai3d_client, models, credential.Credential, TencentCloudSDKException


def _load_ark_sdk() -> Any:
    """Import the Volcengine Ark SDK lazily."""
    try:
        from volcenginesdkarkruntime import Ark
    except ImportError as exc:
        raise RuntimeError(
            "Volcengine Ark SDK is not available. Install `volcengine-python-sdk[ark]`."
        ) from exc
    return Ark


def _image_file_to_base64(image_path: str) -> str:
    """Encode one workspace image file into base64."""
    resolved = resolve_workspace_path(image_path)
    mime_type, _ = mimetypes.guess_type(resolved.name)
    if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
        logger.warning(
            "hy3d input image mime type is {} for {}. Tencent docs only mention jpeg/png/webp.",
            mime_type or "unknown",
            resolved,
        )
    return base64.b64encode(resolved.read_bytes()).decode("utf-8")


def _image_file_to_data_url(image_path: str) -> str:
    """Encode one workspace image file into a data URL for Ark image_url inputs."""
    resolved = resolve_workspace_path(image_path)
    mime_type, _ = mimetypes.guess_type(resolved.name)
    if not mime_type:
        mime_type = "image/png"
    encoded = base64.b64encode(resolved.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _build_client_from_env() -> tuple[Any, Any, Any]:
    """Create one Tencent Cloud AI3D client from config with env fallback."""
    ai3d_client, models, credential_cls, sdk_exception_cls = _load_tencentcloud_sdk()
    app_config = load_app_config(reload=True)
    secret_id = (
        str(app_config.services.tencentcloud_secret_id).strip()
        or os.getenv("TENCENTCLOUD_SECRET_ID", "").strip()
    )
    secret_key = (
        str(app_config.services.tencentcloud_secret_key).strip()
        or os.getenv("TENCENTCLOUD_SECRET_KEY", "").strip()
    )
    session_token = (
        str(app_config.services.tencentcloud_session_token).strip()
        or os.getenv("TENCENTCLOUD_SESSION_TOKEN", "").strip()
        or None
    )
    region = (
        str(app_config.services.tencentcloud_region).strip()
        or os.getenv("TENCENTCLOUD_REGION", "").strip()
        or DEFAULT_REGION
    )

    if not secret_id or not secret_key:
        raise RuntimeError(
            "Missing Tencent Cloud 3D credentials. Set "
            "`services.tencentcloud_secret_id` and `services.tencentcloud_secret_key` "
            "in ~/.creative-claw/conf.json, or export "
            "`TENCENTCLOUD_SECRET_ID` and `TENCENTCLOUD_SECRET_KEY`."
        )

    credential = credential_cls(secret_id, secret_key, session_token)
    return ai3d_client.Ai3dClient(credential, region), models, sdk_exception_cls


def _build_ark_client_from_env() -> Any:
    """Create one Volcengine Ark client from config with env fallback."""
    ark_cls = _load_ark_sdk()
    app_config = load_app_config(reload=True)
    ark_api_key = (
        str(app_config.services.ark_api_key).strip()
        or os.getenv("ARK_API_KEY", "").strip()
    )
    if not ark_api_key:
        raise RuntimeError(
            "Missing Volcengine Ark API key. Set `services.ark_api_key` in "
            "~/.creative-claw/conf.json, or export `ARK_API_KEY`."
        )
    return ark_cls(base_url=ARK_BASE_URL, api_key=ark_api_key)


def _build_submit_request(
    models: Any,
    *,
    prompt: str | None,
    input_path: str | None,
    model: str,
    enable_pbr: bool,
    generate_type: str,
    face_count: int | None,
    polygon_type: str | None,
    result_format: str | None,
) -> Any:
    """Build one validated `SubmitHunyuanTo3DProJob` request."""
    normalized_prompt = str(prompt or "").strip()
    normalized_generate_type = normalize_generate_type(generate_type)
    has_prompt = bool(normalized_prompt)
    has_image = bool(input_path)

    if not has_prompt and not has_image:
        raise ValueError("You must provide either `prompt` or `input_path`.")
    if has_prompt and has_image and normalized_generate_type != "Sketch":
        raise ValueError(
            "Prompt and image can be combined only when `generate_type` is `sketch`."
        )

    request = models.SubmitHunyuanTo3DProJobRequest()
    request.Model = str(model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    request.EnablePBR = enable_pbr
    request.GenerateType = normalized_generate_type

    if has_prompt:
        request.Prompt = normalized_prompt
    if has_image and input_path:
        request.ImageBase64 = _image_file_to_base64(input_path)
    if face_count is not None:
        request.FaceCount = int(face_count)
    if polygon_type:
        request.PolygonType = str(polygon_type).strip()
    normalized_result_format = normalize_result_format(result_format)
    if normalized_result_format:
        request.ResultFormat = normalized_result_format

    return request


def _submit_job_sync(client: Any, request: Any) -> str:
    """Submit one hy3d job synchronously."""
    response = client.SubmitHunyuanTo3DProJob(request)
    job_id = str(getattr(response, "JobId", "") or "").strip()
    if not job_id:
        raise RuntimeError(f"hy3d submit succeeded without JobId: {response.to_json_string()}")
    return job_id


def _query_job_sync(client: Any, models: Any, job_id: str) -> Any:
    """Query one hy3d job synchronously."""
    request = models.QueryHunyuanTo3DProJobRequest()
    request.JobId = job_id
    return client.QueryHunyuanTo3DProJob(request)


async def _poll_job_until_finished(
    client: Any,
    models: Any,
    *,
    job_id: str,
    timeout_seconds: int,
    interval_seconds: int,
) -> Any:
    """Poll the hy3d query API until the job reaches a terminal state."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds

    while loop.time() < deadline:
        response = await asyncio.to_thread(_query_job_sync, client, models, job_id)
        status = str(getattr(response, "Status", "") or "").strip().upper()
        logger.info("hy3d job_id={} status={}", job_id, status)
        if status in TERMINAL_STATUSES:
            return response
        await asyncio.sleep(interval_seconds)

    raise TimeoutError(f"hy3d polling timed out after {timeout_seconds} seconds, job_id={job_id}")


def _infer_download_name(url: str, file_type: str, index: int) -> str:
    """Infer one stable local filename for a returned 3D file."""
    suffix = Path(urlsplit(url).path).suffix
    if not suffix:
        suffix = ".bin"
    safe_type = _safe_segment(str(file_type or "unknown").strip().lower() or "unknown")
    return f"hy3d_result_{index}_{safe_type}{suffix}"


def _download_result_files_sync(result_files: list[Any], download_dir: Path) -> list[dict[str, Any]]:
    """Download returned hy3d files to the target directory."""
    download_dir.mkdir(parents=True, exist_ok=True)
    downloaded_files: list[dict[str, Any]] = []

    for index, item in enumerate(result_files, start=1):
        url = str(getattr(item, "Url", "") or "").strip()
        if not url:
            continue

        output_path = download_dir / _infer_download_name(url, getattr(item, "Type", ""), index)
        with requests.get(url, stream=True, timeout=120) as response:
            response.raise_for_status()
            with output_path.open("wb") as file_obj:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file_obj.write(chunk)

        downloaded_files.append(
            {
                "path": output_path.resolve(),
                "type": str(getattr(item, "Type", "") or "").strip(),
                "url": url,
                "preview_image_url": str(getattr(item, "PreviewImageUrl", "") or "").strip(),
            }
        )

    return downloaded_files


def _submit_ark_3d_task_sync(
    client: Any,
    *,
    provider_name: str,
    model: str,
    content: list[dict[str, Any]],
) -> str:
    """Submit one Ark 3D content-generation task synchronously."""
    response = client.content_generation.tasks.create(model=model, content=content)
    task_id = str(_get_value(response, "id", "ID") or "").strip()
    if not task_id:
        raise RuntimeError(
            f"{provider_name} submit succeeded without task id: {_stringify_sdk_object(response)}"
        )
    return task_id


def _submit_seed3d_task_sync(client: Any, *, model: str, content: list[dict[str, Any]]) -> str:
    """Submit one Seed3D Ark content-generation task synchronously."""
    return _submit_ark_3d_task_sync(
        client,
        provider_name="Seed3D",
        model=model,
        content=content,
    )


def _query_ark_3d_task_sync(client: Any, task_id: str) -> Any:
    """Query one Ark 3D content-generation task synchronously."""
    return client.content_generation.tasks.get(task_id=task_id)


def _query_seed3d_task_sync(client: Any, task_id: str) -> Any:
    """Query one Seed3D Ark content-generation task synchronously."""
    return _query_ark_3d_task_sync(client, task_id)


async def _poll_ark_3d_task_until_finished(
    client: Any,
    *,
    task_id: str,
    provider_name: str,
    timeout_seconds: int,
    interval_seconds: int,
) -> Any:
    """Poll Ark until one 3D generation task reaches a terminal state."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds

    while loop.time() < deadline:
        response = await asyncio.to_thread(_query_ark_3d_task_sync, client, task_id)
        status = str(_get_value(response, "status", "Status") or "").strip().lower()
        logger.info("{} task_id={} status={}", provider_name, task_id, status)
        if status in ARK_3D_TERMINAL_STATUSES:
            return response
        await asyncio.sleep(interval_seconds)

    raise TimeoutError(
        f"{provider_name} polling timed out after {timeout_seconds} seconds, task_id={task_id}"
    )


async def _poll_seed3d_task_until_finished(
    client: Any,
    *,
    task_id: str,
    timeout_seconds: int,
    interval_seconds: int,
) -> Any:
    """Poll Ark until the Seed3D task reaches a terminal state."""
    return await _poll_ark_3d_task_until_finished(
        client,
        task_id=task_id,
        provider_name="seed3d",
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
    )


def _build_seed3d_content(
    *,
    input_path: str | None,
    image_url: str | None,
    subdivision_level: str,
    file_format: str,
) -> list[dict[str, Any]]:
    """Build the Ark task content payload for one Seed3D image-to-3D request."""
    normalized_path = str(input_path or "").strip()
    normalized_image_url = str(image_url or "").strip()
    if bool(normalized_path) == bool(normalized_image_url):
        raise ValueError("Seed3D requires exactly one of `input_path` or `image_url`.")

    resolved_image_url = (
        _image_file_to_data_url(normalized_path)
        if normalized_path
        else normalized_image_url
    )
    return [
        {
            "type": "text",
            "text": _build_seed3d_parameter_text(
                subdivision_level=subdivision_level,
                file_format=file_format,
            ),
        },
        {"type": "image_url", "image_url": {"url": resolved_image_url}},
    ]


def _normalize_image_url_items(
    *,
    input_paths: list[str] | None = None,
    image_urls: list[str] | None = None,
    allow_local_paths: bool,
    provider_name: str,
) -> list[str]:
    """Normalize image URLs for Ark 3D content payloads."""
    raw_paths: Any = input_paths or []
    raw_urls: Any = image_urls or []
    if isinstance(raw_paths, str):
        raw_paths = [raw_paths]
    if isinstance(raw_urls, str):
        raw_urls = [raw_urls]
    normalized_paths = [str(path).strip() for path in raw_paths if str(path).strip()]
    normalized_urls = [str(url).strip() for url in raw_urls if str(url).strip()]
    if normalized_paths and not allow_local_paths:
        raise ValueError(f"{provider_name} supports only externally accessible image URLs.")

    resolved_urls = []
    if allow_local_paths:
        resolved_urls.extend(_image_file_to_data_url(path) for path in normalized_paths)
    resolved_urls.extend(normalized_urls)
    return resolved_urls


def _build_hyper3d_content(
    *,
    prompt: str | None,
    input_paths: list[str] | None,
    image_urls: list[str] | None,
    file_format: str,
    subdivision_level: str | None = None,
    material: str | None = None,
    mesh_mode: str | None = None,
    quality_override: int | None = None,
    addons: str | None = None,
    use_original_alpha: bool | None = None,
    bbox_condition: Any = None,
    ta_pose: bool | None = None,
    hd_texture: bool | None = None,
) -> list[dict[str, Any]]:
    """Build the Ark task content payload for one Hyper3D request."""
    resolved_image_urls = _normalize_image_url_items(
        input_paths=input_paths,
        image_urls=image_urls,
        allow_local_paths=True,
        provider_name="Hyper3D",
    )
    if len(resolved_image_urls) > 5:
        raise ValueError("Hyper3D supports at most 5 input images.")

    text = _build_hyper3d_parameter_text(
        prompt=prompt,
        file_format=file_format,
        subdivision_level=subdivision_level,
        material=material,
        mesh_mode=mesh_mode,
        quality_override=quality_override,
        addons=addons,
        use_original_alpha=use_original_alpha,
        bbox_condition=bbox_condition,
        ta_pose=ta_pose,
        hd_texture=hd_texture,
    )
    if not text and not resolved_image_urls:
        raise ValueError("Hyper3D requires a prompt, an image, or image URLs.")

    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    content.extend({"type": "image_url", "image_url": {"url": url}} for url in resolved_image_urls)
    return content


def _build_hitem3d_content(
    *,
    image_urls: list[str] | None,
    file_format: str,
    resolution: str,
    face_count: int | None = None,
    request_type: int | None = None,
    multi_images_bit: str | None = None,
) -> list[dict[str, Any]]:
    """Build the Ark task content payload for one Hitem3D image-to-3D request."""
    resolved_image_urls = _normalize_image_url_items(
        input_paths=[],
        image_urls=image_urls,
        allow_local_paths=False,
        provider_name="Hitem3D",
    )
    if not resolved_image_urls:
        raise ValueError("Hitem3D requires at least one externally accessible image URL.")
    if len(resolved_image_urls) > 4:
        raise ValueError("Hitem3D supports at most 4 input images.")

    text = _build_hitem3d_parameter_text(
        file_format=file_format,
        resolution=resolution,
        face_count=face_count,
        request_type=request_type,
        multi_images_bit=multi_images_bit,
        image_count=len(resolved_image_urls),
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    content.extend({"type": "image_url", "image_url": {"url": url}} for url in resolved_image_urls)
    return content


def _stringify_sdk_object(value: Any) -> str:
    """Return a compact string for one SDK object or payload."""
    if hasattr(value, "to_json_string"):
        try:
            return str(value.to_json_string())
        except Exception:
            pass
    return repr(value)


def _get_value(value: Any, *names: str) -> Any:
    """Return the first matching dict key or object attribute value."""
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            try:
                return getattr(value, name)
            except Exception:
                pass
    return None


def _to_plain_data(value: Any, *, _seen: set[int] | None = None) -> Any:
    """Convert SDK objects into plain Python containers for defensive parsing."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    _seen = _seen or set()
    value_id = id(value)
    if value_id in _seen:
        return None
    _seen.add(value_id)

    if isinstance(value, dict):
        return {str(key): _to_plain_data(item, _seen=_seen) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_plain_data(item, _seen=_seen) for item in value]

    for method_name in ("model_dump", "to_dict"):
        method = getattr(value, method_name, None)
        if callable(method):
            try:
                return _to_plain_data(method(), _seen=_seen)
            except Exception:
                pass

    to_json_string = getattr(value, "to_json_string", None)
    if callable(to_json_string):
        try:
            return _to_plain_data(json.loads(to_json_string()), _seen=_seen)
        except Exception:
            pass

    attributes: dict[str, Any] = {}
    for attr_name in (
        "content",
        "data",
        "error",
        "files",
        "output",
        "result",
        "url",
        "urls",
    ):
        if hasattr(value, attr_name):
            try:
                attributes[attr_name] = getattr(value, attr_name)
            except Exception:
                pass
    if attributes:
        return _to_plain_data(attributes, _seen=_seen)

    if hasattr(value, "__dict__"):
        return _to_plain_data(
            {
                key: item
                for key, item in vars(value).items()
                if not str(key).startswith("_")
            },
            _seen=_seen,
        )

    return None


def _is_seed3d_result_url(*, key_path: str, url: str) -> bool:
    """Return whether one URL-like field appears to point at a 3D result asset."""
    url_path = urlsplit(url).path.lower()
    suffix = Path(url_path).suffix
    if suffix in _ARK_3D_DOWNLOAD_SUFFIXES:
        return True

    normalized_key_path = key_path.lower()
    if any(marker in normalized_key_path for marker in _ARK_3D_URL_EXCLUDE_MARKERS):
        return False
    return any(marker in normalized_key_path for marker in _ARK_3D_URL_INCLUDE_MARKERS)


def _infer_seed3d_file_type(url: str, file_format: str) -> str:
    """Infer a compact result file type for one Ark 3D URL."""
    suffix = Path(urlsplit(url).path).suffix.lower().lstrip(".")
    if suffix and suffix != "zip":
        return suffix
    normalized_format = str(file_format or "").strip().lower()
    if normalized_format in (_SEED3D_FILE_FORMATS | _HYPER3D_FILE_FORMATS | _HITEM3D_FILE_FORMATS):
        return normalized_format
    return DEFAULT_SEED3D_FILE_FORMAT


def _collect_seed3d_url_records(
    value: Any,
    *,
    file_format: str,
    key_path: str = "",
) -> list[dict[str, str]]:
    """Collect likely downloadable 3D URLs from a plain task-result payload."""
    records: list[dict[str, str]] = []
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.startswith(("http://", "https://")) and _is_seed3d_result_url(
            key_path=key_path,
            url=normalized,
        ):
            records.append(
                {
                    "url": normalized,
                    "type": _infer_seed3d_file_type(normalized, file_format),
                    "preview_image_url": "",
                }
            )
        return records

    if isinstance(value, list):
        for index, item in enumerate(value):
            records.extend(
                _collect_seed3d_url_records(
                    item,
                    file_format=file_format,
                    key_path=f"{key_path}[{index}]",
                )
            )
        return records

    if isinstance(value, dict):
        for key, item in value.items():
            child_key_path = f"{key_path}.{key}" if key_path else str(key)
            records.extend(
                _collect_seed3d_url_records(
                    item,
                    file_format=file_format,
                    key_path=child_key_path,
                )
            )
        return records

    return records


def _extract_seed3d_result_files(task_result: Any, *, file_format: str) -> list[dict[str, str]]:
    """Extract downloadable Seed3D result file records from one task response."""
    plain_data = _to_plain_data(task_result)
    records = _collect_seed3d_url_records(plain_data, file_format=file_format)
    unique_records: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for record in records:
        url = record["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        unique_records.append(record)
    return unique_records


def _extract_seed3d_error(task_result: Any) -> str:
    """Extract a readable Seed3D failure reason from one task response."""
    plain_data = _to_plain_data(task_result)
    if isinstance(plain_data, dict):
        error_obj = plain_data.get("error") or plain_data.get("Error")
        if error_obj:
            return str(error_obj)
    return _stringify_sdk_object(task_result)


def _infer_ark_3d_download_name(
    url: str,
    file_format: str,
    index: int,
    provider_prefix: str,
) -> str:
    """Infer one stable local filename for an Ark 3D result file."""
    suffix = Path(urlsplit(url).path).suffix
    if not suffix:
        normalized_format = str(file_format or "").strip().lower()
        if normalized_format not in (_SEED3D_FILE_FORMATS | _HYPER3D_FILE_FORMATS | _HITEM3D_FILE_FORMATS):
            normalized_format = DEFAULT_SEED3D_FILE_FORMAT
        suffix = f".{normalized_format}"
    return f"{_safe_segment(provider_prefix)}_result_{index}{suffix}"


def _infer_seed3d_download_name(url: str, file_format: str, index: int) -> str:
    """Infer one stable local filename for a Seed3D result file."""
    return _infer_ark_3d_download_name(url, file_format, index, "seed3d")


def _download_seed3d_result_files_sync(
    result_files: list[dict[str, str]],
    download_dir: Path,
    *,
    file_format: str,
    provider_prefix: str = "seed3d",
) -> list[dict[str, Any]]:
    """Download returned Ark 3D files to the target directory."""
    download_dir.mkdir(parents=True, exist_ok=True)
    downloaded_files: list[dict[str, Any]] = []

    for index, item in enumerate(result_files, start=1):
        url = str(item.get("url", "")).strip()
        if not url:
            continue

        output_path = download_dir / _infer_ark_3d_download_name(
            url,
            file_format,
            index,
            provider_prefix,
        )
        with requests.get(url, stream=True, timeout=120) as response:
            response.raise_for_status()
            with output_path.open("wb") as file_obj:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file_obj.write(chunk)

        downloaded_files.append(
            {
                "path": output_path.resolve(),
                "type": str(item.get("type", "")).strip(),
                "url": url,
                "preview_image_url": str(item.get("preview_image_url", "")).strip(),
            }
        )

    return downloaded_files


async def hy3d_generate_tool(
    *,
    prompt: str | None,
    input_path: str | None,
    model: str = DEFAULT_MODEL,
    enable_pbr: bool = False,
    generate_type: str = DEFAULT_GENERATE_TYPE,
    face_count: int | None = None,
    polygon_type: str | None = None,
    result_format: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    session_id: str,
    turn_index: int,
    step: int,
) -> dict[str, Any]:
    """Run one full hy3d generation job through Tencent Cloud SDK."""
    logger.info("calling hy3d 3d generation tool ...")
    sdk_exception_cls: Any = None

    try:
        client, models, sdk_exception_cls = _build_client_from_env()
        request = _build_submit_request(
            models,
            prompt=prompt,
            input_path=input_path,
            model=model,
            enable_pbr=enable_pbr,
            generate_type=generate_type,
            face_count=face_count,
            polygon_type=polygon_type,
            result_format=result_format,
        )

        job_id = await asyncio.to_thread(_submit_job_sync, client, request)
        query_response = await _poll_job_until_finished(
            client,
            models,
            job_id=job_id,
            timeout_seconds=int(timeout_seconds),
            interval_seconds=int(interval_seconds),
        )
    except Exception as exc:
        if sdk_exception_cls is not None and isinstance(exc, sdk_exception_cls):
            return {
                "status": "error",
                "message": f"TencentCloudSDKException: code={exc.code}, message={exc.message}",
                "provider": "hy3d",
                "model_name": str(model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            }
        logger.opt(exception=exc).error(
            "hy3d generation failed before completion: error_type={} error={!r}",
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": f"hy3d generation failed: {exc}",
            "provider": "hy3d",
            "model_name": str(model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        }

    status = str(getattr(query_response, "Status", "") or "").strip().upper()
    error_code = str(getattr(query_response, "ErrorCode", "") or "").strip()
    error_message = str(getattr(query_response, "ErrorMessage", "") or "").strip()
    if status != "DONE":
        detail = f"status={status}"
        if error_code:
            detail += f", error_code={error_code}"
        if error_message:
            detail += f", error_message={error_message}"
        return {
            "status": "error",
            "message": f"hy3d job failed: {detail}",
            "provider": "hy3d",
            "model_name": str(model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            "job_id": job_id,
        }

    result_files = list(getattr(query_response, "ResultFile3Ds", []) or [])
    download_dir = _build_download_dir(
        session_id=session_id,
        turn_index=turn_index,
        step=step,
        job_id=job_id,
    )

    try:
        downloaded_files = await asyncio.to_thread(
            _download_result_files_sync,
            result_files,
            download_dir,
        )
    except Exception as exc:
        logger.opt(exception=exc).error(
            "hy3d result download failed: error_type={} error={!r}",
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": f"hy3d job succeeded but result download failed: {exc}",
            "provider": "hy3d",
            "model_name": str(model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            "job_id": job_id,
        }

    if not downloaded_files:
        return {
            "status": "error",
            "message": "hy3d job succeeded but returned no downloadable result files.",
            "provider": "hy3d",
            "model_name": str(model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            "job_id": job_id,
        }

    return {
        "status": "success",
        "message": f"hy3d job {job_id} succeeded with {len(downloaded_files)} file(s).",
        "provider": "hy3d",
        "model_name": str(model or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "job_id": job_id,
        "generate_type": normalize_generate_type(generate_type),
        "download_dir": download_dir,
        "downloaded_files": downloaded_files,
    }


async def seed3d_generate_tool(
    *,
    input_path: str | None,
    image_url: str | None = None,
    model: str = DEFAULT_SEED3D_MODEL,
    file_format: str = DEFAULT_SEED3D_FILE_FORMAT,
    subdivision_level: str = DEFAULT_SEED3D_SUBDIVISION_LEVEL,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    interval_seconds: int = DEFAULT_SEED3D_INTERVAL_SECONDS,
    session_id: str,
    turn_index: int,
    step: int,
) -> dict[str, Any]:
    """Run one full Seed3D image-to-3D generation task through Volcengine Ark."""
    logger.info("calling seed3d 3d generation tool ...")
    current_model = str(model or DEFAULT_SEED3D_MODEL).strip() or DEFAULT_SEED3D_MODEL
    current_file_format = normalize_seed3d_file_format(file_format)
    current_subdivision_level = normalize_seed3d_subdivision_level(subdivision_level)
    task_id = ""

    try:
        client = _build_ark_client_from_env()
        content = _build_seed3d_content(
            input_path=input_path,
            image_url=image_url,
            subdivision_level=current_subdivision_level,
            file_format=current_file_format,
        )
        task_id = await asyncio.to_thread(
            _submit_seed3d_task_sync,
            client,
            model=current_model,
            content=content,
        )
        task_result = await _poll_seed3d_task_until_finished(
            client,
            task_id=task_id,
            timeout_seconds=int(timeout_seconds),
            interval_seconds=int(interval_seconds),
        )
    except Exception as exc:
        logger.opt(exception=exc).error(
            "seed3d generation failed before completion: error_type={} error={!r}",
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": f"Seed3D generation failed: {exc}",
            "provider": "seed3d",
            "model_name": current_model,
            "job_id": task_id,
        }

    status = str(_get_value(task_result, "status", "Status") or "").strip().lower()
    if status != "succeeded":
        return {
            "status": "error",
            "message": f"Seed3D task failed: {_extract_seed3d_error(task_result)}",
            "provider": "seed3d",
            "model_name": current_model,
            "job_id": task_id,
        }

    result_files = _extract_seed3d_result_files(
        task_result,
        file_format=current_file_format,
    )
    if not result_files:
        return {
            "status": "error",
            "message": "Seed3D task succeeded but returned no downloadable 3D result files.",
            "provider": "seed3d",
            "model_name": current_model,
            "job_id": task_id,
        }

    download_dir = _build_download_dir(
        session_id=session_id,
        turn_index=turn_index,
        step=step,
        job_id=task_id,
    )
    try:
        downloaded_files = await asyncio.to_thread(
            _download_seed3d_result_files_sync,
            result_files,
            download_dir,
            file_format=current_file_format,
        )
    except Exception as exc:
        logger.opt(exception=exc).error(
            "seed3d result download failed: error_type={} error={!r}",
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": f"Seed3D task succeeded but result download failed: {exc}",
            "provider": "seed3d",
            "model_name": current_model,
            "job_id": task_id,
        }

    if not downloaded_files:
        return {
            "status": "error",
            "message": "Seed3D task succeeded but returned no downloadable result files.",
            "provider": "seed3d",
            "model_name": current_model,
            "job_id": task_id,
        }

    return {
        "status": "success",
        "message": f"Seed3D task {task_id} succeeded with {len(downloaded_files)} file(s).",
        "provider": "seed3d",
        "model_name": current_model,
        "job_id": task_id,
        "generate_type": "image_to_3d",
        "file_format": current_file_format,
        "subdivision_level": current_subdivision_level,
        "download_dir": download_dir,
        "downloaded_files": downloaded_files,
    }


async def _run_ark_3d_generation_task(
    *,
    provider: str,
    display_name: str,
    model: str,
    content: list[dict[str, Any]],
    file_format: str,
    generate_type: str,
    timeout_seconds: int,
    interval_seconds: int,
    session_id: str,
    turn_index: int,
    step: int,
) -> dict[str, Any]:
    """Run one full Ark 3D generation task and download its result file."""
    task_id = ""

    try:
        client = _build_ark_client_from_env()
        task_id = await asyncio.to_thread(
            _submit_ark_3d_task_sync,
            client,
            provider_name=display_name,
            model=model,
            content=content,
        )
        task_result = await _poll_ark_3d_task_until_finished(
            client,
            task_id=task_id,
            provider_name=provider,
            timeout_seconds=int(timeout_seconds),
            interval_seconds=int(interval_seconds),
        )
    except Exception as exc:
        logger.opt(exception=exc).error(
            "{} generation failed before completion: error_type={} error={!r}",
            provider,
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": f"{display_name} generation failed: {exc}",
            "provider": provider,
            "model_name": model,
            "job_id": task_id,
        }

    status = str(_get_value(task_result, "status", "Status") or "").strip().lower()
    if status != "succeeded":
        return {
            "status": "error",
            "message": f"{display_name} task failed: {_extract_seed3d_error(task_result)}",
            "provider": provider,
            "model_name": model,
            "job_id": task_id,
        }

    result_files = _extract_seed3d_result_files(task_result, file_format=file_format)
    if not result_files:
        return {
            "status": "error",
            "message": f"{display_name} task succeeded but returned no downloadable 3D result files.",
            "provider": provider,
            "model_name": model,
            "job_id": task_id,
        }

    download_dir = _build_download_dir(
        session_id=session_id,
        turn_index=turn_index,
        step=step,
        job_id=task_id,
    )
    try:
        downloaded_files = await asyncio.to_thread(
            _download_seed3d_result_files_sync,
            result_files,
            download_dir,
            file_format=file_format,
            provider_prefix=provider,
        )
    except Exception as exc:
        logger.opt(exception=exc).error(
            "{} result download failed: error_type={} error={!r}",
            provider,
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": f"{display_name} task succeeded but result download failed: {exc}",
            "provider": provider,
            "model_name": model,
            "job_id": task_id,
        }

    if not downloaded_files:
        return {
            "status": "error",
            "message": f"{display_name} task succeeded but returned no downloadable result files.",
            "provider": provider,
            "model_name": model,
            "job_id": task_id,
        }

    return {
        "status": "success",
        "message": f"{display_name} task {task_id} succeeded with {len(downloaded_files)} file(s).",
        "provider": provider,
        "model_name": model,
        "job_id": task_id,
        "generate_type": generate_type,
        "file_format": file_format,
        "download_dir": download_dir,
        "downloaded_files": downloaded_files,
    }


async def hyper3d_generate_tool(
    *,
    prompt: str | None,
    input_paths: list[str] | None = None,
    image_urls: list[str] | None = None,
    model: str = DEFAULT_HYPER3D_MODEL,
    file_format: str = DEFAULT_HYPER3D_FILE_FORMAT,
    subdivision_level: str | None = None,
    material: str | None = None,
    mesh_mode: str | None = None,
    quality_override: int | None = None,
    addons: str | None = None,
    use_original_alpha: bool | None = None,
    bbox_condition: Any = None,
    ta_pose: bool | None = None,
    hd_texture: bool | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    interval_seconds: int = DEFAULT_HYPER3D_INTERVAL_SECONDS,
    session_id: str,
    turn_index: int,
    step: int,
) -> dict[str, Any]:
    """Run one full Hyper3D text/image-to-3D generation task through Volcengine Ark."""
    logger.info("calling hyper3d 3d generation tool ...")
    current_model = str(model or DEFAULT_HYPER3D_MODEL).strip() or DEFAULT_HYPER3D_MODEL
    current_file_format = normalize_hyper3d_file_format(file_format)
    current_subdivision_level = (
        normalize_hyper3d_subdivision_level(subdivision_level)
        if subdivision_level not in (None, "")
        else ""
    )

    try:
        content = _build_hyper3d_content(
            prompt=prompt,
            input_paths=input_paths,
            image_urls=image_urls,
            file_format=current_file_format,
            subdivision_level=current_subdivision_level,
            material=material,
            mesh_mode=mesh_mode,
            quality_override=quality_override,
            addons=addons,
            use_original_alpha=use_original_alpha,
            bbox_condition=bbox_condition,
            ta_pose=ta_pose,
            hd_texture=hd_texture,
        )
    except Exception as exc:
        logger.opt(exception=exc).error("hyper3d content validation failed")
        return {
            "status": "error",
            "message": f"Hyper3D generation failed: {exc}",
            "provider": "hyper3d",
            "model_name": current_model,
            "job_id": "",
        }

    generate_type = "image_to_3d" if input_paths or image_urls else "text_to_3d"
    result = await _run_ark_3d_generation_task(
        provider="hyper3d",
        display_name="Hyper3D",
        model=current_model,
        content=content,
        file_format=current_file_format,
        generate_type=generate_type,
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        session_id=session_id,
        turn_index=turn_index,
        step=step,
    )
    if result["status"] == "success":
        result["subdivision_level"] = current_subdivision_level
    return result


async def hitem3d_generate_tool(
    *,
    image_urls: list[str] | None = None,
    model: str = DEFAULT_HITEM3D_MODEL,
    file_format: str = DEFAULT_HITEM3D_FILE_FORMAT,
    resolution: str = DEFAULT_HITEM3D_RESOLUTION,
    face_count: int | None = None,
    request_type: int | None = None,
    multi_images_bit: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    interval_seconds: int = DEFAULT_HITEM3D_INTERVAL_SECONDS,
    session_id: str,
    turn_index: int,
    step: int,
) -> dict[str, Any]:
    """Run one full Hitem3D image-to-3D generation task through Volcengine Ark."""
    logger.info("calling hitem3d 3d generation tool ...")
    current_model = str(model or DEFAULT_HITEM3D_MODEL).strip() or DEFAULT_HITEM3D_MODEL
    current_file_format = normalize_hitem3d_file_format(file_format)
    current_resolution = normalize_hitem3d_resolution(resolution)

    try:
        content = _build_hitem3d_content(
            image_urls=image_urls,
            file_format=current_file_format,
            resolution=current_resolution,
            face_count=face_count,
            request_type=request_type,
            multi_images_bit=multi_images_bit,
        )
    except Exception as exc:
        logger.opt(exception=exc).error("hitem3d content validation failed")
        return {
            "status": "error",
            "message": f"Hitem3D generation failed: {exc}",
            "provider": "hitem3d",
            "model_name": current_model,
            "job_id": "",
        }

    result = await _run_ark_3d_generation_task(
        provider="hitem3d",
        display_name="Hitem3D",
        model=current_model,
        content=content,
        file_format=current_file_format,
        generate_type="image_to_3d",
        timeout_seconds=timeout_seconds,
        interval_seconds=interval_seconds,
        session_id=session_id,
        turn_index=turn_index,
        step=step,
    )
    if result["status"] == "success":
        result["resolution"] = current_resolution
    return result
