"""Deterministic video basic operation dispatch helpers."""

from __future__ import annotations

import json
from typing import Any

from src.agents.experts.basic_operations_helpers import (
    build_error_output,
    build_file_output,
    build_text_output,
    normalize_input_path,
    normalize_input_paths,
    normalize_required_string,
)
from src.tools.builtin_tools import BuiltinToolbox


_SUPPORTED_VIDEO_OPERATIONS = {
    "info",
    "extract_frame",
    "trim",
    "concat",
    "convert",
}


def run_video_basic_operation(parameters: dict[str, Any]) -> dict[str, Any]:
    """Run one deterministic video basic operation and normalize the result."""
    expert_name = "VideoBasicOperations"
    try:
        operation = normalize_required_string(parameters.get("operation"), "operation").lower()
        if operation not in _SUPPORTED_VIDEO_OPERATIONS:
            raise ValueError(
                f"Unsupported operation: {operation}. "
                f"Allowed values: {sorted(_SUPPORTED_VIDEO_OPERATIONS)}"
            )

        toolbox = BuiltinToolbox()
        if operation == "concat":
            input_paths = normalize_input_paths(parameters)
            result = toolbox.video_concat(
                input_paths,
                output_format=(
                    str(parameters.get("output_format")).strip()
                    if parameters.get("output_format") is not None
                    else None
                ),
            )
        else:
            input_path = normalize_input_path(parameters)
            input_paths = [input_path]
            if operation == "info":
                result = toolbox.video_info(input_path)
            elif operation == "extract_frame":
                result = toolbox.video_extract_frame(
                    input_path,
                    timestamp=normalize_required_string(parameters.get("timestamp"), "timestamp"),
                    output_format=str(parameters.get("output_format", "png")).strip() or "png",
                )
            elif operation == "trim":
                result = toolbox.video_trim(
                    input_path,
                    start_time=normalize_required_string(parameters.get("start_time"), "start_time"),
                    end_time=(
                        str(parameters.get("end_time")).strip()
                        if parameters.get("end_time") is not None
                        else None
                    ),
                    duration=(
                        str(parameters.get("duration")).strip()
                        if parameters.get("duration") is not None
                        else None
                    ),
                )
            else:
                result = toolbox.video_convert(
                    input_path,
                    output_format=normalize_required_string(parameters.get("output_format"), "output_format"),
                    video_codec=(
                        str(parameters.get("video_codec")).strip()
                        if parameters.get("video_codec") is not None
                        else None
                    ),
                    audio_codec=(
                        str(parameters.get("audio_codec")).strip()
                        if parameters.get("audio_codec") is not None
                        else None
                    ),
                )

        if str(result).startswith("Error"):
            return build_error_output(expert_name, str(result))

        if operation == "info":
            structured_result = json.loads(str(result))
            return build_text_output(
                expert_name,
                operation,
                structured_result,
                output_text=str(result),
            )

        return build_file_output(
            expert_name,
            operation,
            str(result),
            description=f"VideoBasicOperations produced this file via `{operation}`.",
            input_paths=input_paths,
        )
    except Exception as exc:
        return build_error_output(expert_name, f"{expert_name} failed: {exc}")
