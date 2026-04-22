"""Deterministic image basic operation dispatch helpers."""

from __future__ import annotations

import json
from typing import Any

from src.agents.experts.basic_operations_helpers import (
    build_error_output,
    build_file_output,
    build_text_output,
    normalize_bool,
    normalize_float,
    normalize_input_path,
    normalize_int,
    normalize_optional_int,
    normalize_required_string,
)
from src.tools.builtin_tools import BuiltinToolbox


_SUPPORTED_IMAGE_OPERATIONS = {
    "crop",
    "rotate",
    "flip",
    "info",
    "resize",
    "convert",
}


def run_image_basic_operation(parameters: dict[str, Any]) -> dict[str, Any]:
    """Run one deterministic image basic operation and normalize the result."""
    expert_name = "ImageBasicOperations"
    try:
        operation = normalize_required_string(parameters.get("operation"), "operation").lower()
        if operation not in _SUPPORTED_IMAGE_OPERATIONS:
            raise ValueError(
                f"Unsupported operation: {operation}. "
                f"Allowed values: {sorted(_SUPPORTED_IMAGE_OPERATIONS)}"
            )

        input_path = normalize_input_path(parameters)
        toolbox = BuiltinToolbox()

        if operation == "crop":
            result = toolbox.image_crop(
                input_path,
                normalize_int(parameters.get("left"), "left"),
                normalize_int(parameters.get("top"), "top"),
                normalize_int(parameters.get("right"), "right"),
                normalize_int(parameters.get("bottom"), "bottom"),
            )
        elif operation == "rotate":
            result = toolbox.image_rotate(
                input_path,
                normalize_float(parameters.get("degrees"), "degrees"),
                expand=normalize_bool(parameters.get("expand"), default=True),
            )
        elif operation == "flip":
            result = toolbox.image_flip(
                input_path,
                normalize_required_string(parameters.get("direction"), "direction"),
            )
        elif operation == "info":
            result = toolbox.image_info(input_path)
        elif operation == "resize":
            result = toolbox.image_resize(
                input_path,
                width=normalize_optional_int(parameters.get("width"), "width"),
                height=normalize_optional_int(parameters.get("height"), "height"),
                keep_aspect_ratio=normalize_bool(
                    parameters.get("keep_aspect_ratio"),
                    default=True,
                ),
                resample=str(parameters.get("resample", "lanczos")).strip() or "lanczos",
            )
        else:
            result = toolbox.image_convert(
                input_path,
                output_format=normalize_required_string(parameters.get("output_format"), "output_format"),
                mode=(str(parameters.get("mode")).strip() if parameters.get("mode") is not None else None),
                quality=normalize_optional_int(parameters.get("quality"), "quality"),
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
            description=f"ImageBasicOperations produced this file via `{operation}`.",
            input_paths=[input_path],
            session_id=str(parameters.get("__session_id", "")).strip(),
            turn_index=int(parameters.get("__turn_index", 0) or 0),
            step=int(parameters.get("__step", 0) or 0),
            expert_step=int(parameters.get("__expert_step", 0) or 0),
        )
    except Exception as exc:
        return build_error_output(expert_name, f"{expert_name} failed: {exc}")
