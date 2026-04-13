"""Provider tools for the 3D generation expert."""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from src.logger import logger
from src.runtime.workspace import generated_session_dir, resolve_workspace_path

DEFAULT_REGION = "ap-guangzhou"
DEFAULT_MODEL = "3.0"
DEFAULT_GENERATE_TYPE = "Normal"
DEFAULT_TIMEOUT_SECONDS = 900
DEFAULT_INTERVAL_SECONDS = 8
TERMINAL_STATUSES = {"DONE", "FAIL"}

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


def _build_download_dir(*, session_id: str, step: int, job_id: str) -> Path:
    """Build the target download directory for one hy3d job."""
    output_dir = generated_session_dir(session_id) / f"step{step}_3d_generation_{_safe_segment(job_id)}"
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


def _build_client_from_env() -> tuple[Any, Any, Any]:
    """Create one Tencent Cloud AI3D client from environment variables."""
    ai3d_client, models, credential_cls, sdk_exception_cls = _load_tencentcloud_sdk()
    secret_id = os.getenv("TENCENTCLOUD_SECRET_ID", "").strip()
    secret_key = os.getenv("TENCENTCLOUD_SECRET_KEY", "").strip()
    session_token = os.getenv("TENCENTCLOUD_SESSION_TOKEN", "").strip() or None
    region = os.getenv("TENCENTCLOUD_REGION", DEFAULT_REGION).strip() or DEFAULT_REGION

    if not secret_id or not secret_key:
        raise RuntimeError("Missing TENCENTCLOUD_SECRET_ID or TENCENTCLOUD_SECRET_KEY.")

    credential = credential_cls(secret_id, secret_key, session_token)
    return ai3d_client.Ai3dClient(credential, region), models, sdk_exception_cls


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
    download_dir = _build_download_dir(session_id=session_id, step=step, job_id=job_id)

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
