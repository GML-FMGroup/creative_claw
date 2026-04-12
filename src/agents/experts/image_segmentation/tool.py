"""DINO-X prompt-to-mask tool integration."""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from google.adk.agents.invocation_context import InvocationContext
import numpy as np
from PIL import Image
from pycocotools import mask as mask_utils

from conf.api import API_CONFIG
from src.logger import logger
from src.runtime.workspace import (
    resolve_workspace_path,
    save_binary_output,
    workspace_relative_path,
)

_BASE_URL = "https://api.deepdataspace.com"
_CREATE_TASK_URL = f"{_BASE_URL}/v2/task/dinox/detection"
_TASK_STATUS_URL_TEMPLATE = f"{_BASE_URL}/v2/task_status/{{task_uuid}}"
_DEFAULT_MODEL = "DINO-X-1.0"
_DEFAULT_THRESHOLD = 0.25
_DEFAULT_TIMEOUT_SECONDS = 120
_DEFAULT_INTERVAL_SECONDS = 2


def _format_exception_summary(exc: Exception) -> str:
    """Return a concise exception summary that always includes the exception type."""
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return type(exc).__name__


def _image_file_to_data_url(image_path: Path) -> str:
    """Convert one local image file into a `data:` URL accepted by DDS."""
    mime_type, _ = mimetypes.guess_type(image_path.name)
    encoded = base64.b64encode(image_path.read_bytes()).decode("utf-8")
    return f"data:{mime_type or 'application/octet-stream'};base64,{encoded}"


def _create_segmentation_task(
    token: str,
    model: str,
    image: str,
    prompt_text: str,
    bbox_threshold: float,
) -> str:
    """Create one async DDS segmentation task and return the task UUID."""
    payload = {
        "model": model,
        "image": image,
        "prompt": {"type": "text", "text": prompt_text},
        "targets": ["bbox", "mask"],
        "mask_format": "coco_rle",
        "bbox_threshold": bbox_threshold,
    }
    headers = {"Token": token, "Content-Type": "application/json"}

    response = requests.post(_CREATE_TASK_URL, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Create task failed: {json.dumps(data, ensure_ascii=False)}")

    task_data = data.get("data") or {}
    task_uuid = task_data.get("task_uuid") or task_data.get("uuid")
    if not task_uuid:
        raise RuntimeError(f"Task uuid missing: {json.dumps(data, ensure_ascii=False)}")
    return str(task_uuid)


def _poll_segmentation_result(
    token: str,
    task_uuid: str,
    *,
    timeout_seconds: int,
    interval_seconds: int,
) -> dict[str, Any]:
    """Poll DDS until the segmentation task succeeds, fails, or times out."""
    headers = {"Token": token}
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        response = requests.get(
            _TASK_STATUS_URL_TEMPLATE.format(task_uuid=task_uuid),
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Query task failed: {json.dumps(data, ensure_ascii=False)}")

        task_data = data.get("data") or {}
        status = str(task_data.get("status", "")).strip().lower()
        logger.info(
            "[image_segmentation_tool] polling task_uuid='{}' status='{}'",
            task_uuid,
            status,
        )

        if status == "success":
            return task_data
        if status in {"failed", "error"}:
            raise RuntimeError(f"Task failed: {json.dumps(task_data, ensure_ascii=False)}")

        time.sleep(interval_seconds)

    raise TimeoutError(f"Polling timed out after {timeout_seconds} seconds, task_uuid={task_uuid}")


def _decode_rle_mask(rle: dict[str, Any]) -> Any:
    """Decode one COCO RLE mask into a binary numpy array."""
    counts = rle.get("counts")
    if isinstance(counts, str):
        return mask_utils.decode({"counts": counts.encode("utf-8"), "size": rle["size"]})
    return mask_utils.decode(rle)


def _merge_masks(image_size: tuple[int, int], objects: list[dict[str, Any]]) -> bytes:
    """Merge all object masks into one binary PNG image."""
    width, height = image_size
    merged = np.zeros((height, width), dtype=np.uint8)

    for obj in objects:
        mask_rle = obj.get("mask")
        if not isinstance(mask_rle, dict):
            continue
        decoded = _decode_rle_mask(mask_rle)
        mask_image = Image.fromarray((decoded > 0).astype(np.uint8) * 255, mode="L")
        if mask_image.size != (width, height):
            mask_image = mask_image.resize((width, height), Image.Resampling.NEAREST)
        merged = np.maximum(merged, np.array(mask_image, dtype=np.uint8))

    buffer = BytesIO()
    Image.fromarray(merged, mode="L").save(buffer, format="PNG")
    return buffer.getvalue()


async def image_segmentation_tool(
    ctx: InvocationContext,
    input_path: str,
    prompt: str,
    *,
    model: str = _DEFAULT_MODEL,
    threshold: float = _DEFAULT_THRESHOLD,
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS,
    interval_seconds: int = _DEFAULT_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """Segment prompt-matched regions in one workspace image and save the mask."""
    tool_name_for_log = "image_segmentation_tool"
    resolved_path = None
    try:
        token = (
            str(API_CONFIG.DDS_API_KEY).strip()
            or os.getenv("DDS_API_KEY")
            or os.getenv("DDS_TOKEN")
        )
        if not token:
            raise RuntimeError("DDS_API_KEY is not set. Please configure it in ~/.creative-claw/conf.json.")

        normalized_prompt = str(prompt).strip()
        if not normalized_prompt:
            raise ValueError("prompt must not be empty.")

        resolved_path = resolve_workspace_path(input_path)
        image = Image.open(resolved_path)
        image_data_url = _image_file_to_data_url(resolved_path)

        logger.info(
            "[{}] called: path='{}', resolved_path='{}', prompt='{}', model='{}', threshold={}",
            tool_name_for_log,
            input_path,
            resolved_path,
            normalized_prompt,
            model,
            threshold,
        )

        task_uuid = await asyncio.to_thread(
            _create_segmentation_task,
            token,
            model,
            image_data_url,
            normalized_prompt,
            threshold,
        )
        task_result = await asyncio.to_thread(
            _poll_segmentation_result,
            token,
            task_uuid,
            timeout_seconds=timeout_seconds,
            interval_seconds=interval_seconds,
        )

        result_payload = task_result.get("result") or {}
        objects = result_payload.get("objects") or []
        mask_png = await asyncio.to_thread(_merge_masks, image.size, objects)
        output_path = save_binary_output(
            mask_png,
            session_id=ctx.session.id,
            step=ctx.session.state.get("step", 0) + 1,
            output_type="segmentation_mask",
            index=0,
            extension=".png",
        )

        message = f"Segmented {len(objects)} object(s) for prompt '{normalized_prompt}'."
        return {
            "status": "success",
            "message": message,
            "input_path": workspace_relative_path(resolved_path),
            "prompt": normalized_prompt,
            "objects": objects,
            "bboxes": [obj.get("bbox") for obj in objects if isinstance(obj, dict) and obj.get("bbox")],
            "task_uuid": task_uuid,
            "session_id": str(task_result.get("session_id", "")).strip(),
            "provider": "deepdataspace",
            "model_name": str(model).strip() or _DEFAULT_MODEL,
            "threshold": float(threshold),
            "mask_path": workspace_relative_path(output_path),
        }
    except Exception as exc:
        error_summary = _format_exception_summary(exc)
        logger.opt(exception=exc).error(
            "[{}] segmentation failed: input_path='{}' resolved_path='{}' prompt='{}' error_summary={}",
            tool_name_for_log,
            input_path,
            resolved_path or "<unresolved>",
            prompt,
            error_summary,
        )
        return {
            "status": "error",
            "message": (
                f"[{tool_name_for_log}] segmentation failed for '{input_path}' "
                f"(resolved='{resolved_path or '<unresolved>'}', prompt='{prompt}'): {error_summary}"
            ),
            "input_path": (
                workspace_relative_path(resolved_path) if resolved_path is not None else str(input_path)
            ),
            "prompt": str(prompt).strip(),
            "objects": [],
            "bboxes": [],
            "provider": "deepdataspace",
            "model_name": str(model).strip() or _DEFAULT_MODEL,
            "threshold": float(threshold),
            "mask_path": "",
        }
