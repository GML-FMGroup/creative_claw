"""Shared helpers for deterministic basic-operation experts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.runtime.workspace import build_workspace_file_record


def build_error_output(expert_name: str, message: str) -> dict[str, Any]:
    """Build one normalized error payload for a basic-operation expert."""
    normalized_message = str(message).strip() or f"{expert_name} failed."
    return {
        "status": "error",
        "message": normalized_message,
        "output_files": [],
    }


def build_text_output(
    expert_name: str,
    operation: str,
    structured_result: dict[str, Any],
    *,
    output_text: str | None = None,
) -> dict[str, Any]:
    """Build one normalized success payload for a metadata-like operation."""
    rendered_text = output_text or json.dumps(structured_result, ensure_ascii=False, indent=2)
    return {
        "status": "success",
        "message": f"{expert_name} completed `{operation}`.",
        "message_for_user": f"{expert_name} completed `{operation}`.",
        "output_text": rendered_text,
        "results": structured_result,
        "output_files": [],
    }


def build_file_output(
    expert_name: str,
    operation: str,
    output_path: str,
    *,
    description: str,
    input_paths: list[str],
) -> dict[str, Any]:
    """Build one normalized success payload for a file-producing operation."""
    artifact_name = Path(output_path).name
    return {
        "status": "success",
        "message": f"{expert_name} completed `{operation}`. Output file: {artifact_name}.",
        "message_for_user": f"{expert_name} completed `{operation}`.",
        "results": {
            "operation": operation,
            "input_paths": input_paths,
            "output_path": output_path,
        },
        "output_files": [
            build_workspace_file_record(
                output_path,
                description=description,
                source="expert",
                name=artifact_name,
            )
        ],
    }


def normalize_bool(value: Any, *, default: bool = False) -> bool:
    """Normalize one optional boolean-like value."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def normalize_int(value: Any, name: str) -> int:
    """Normalize one required integer value."""
    if value is None or str(value).strip() == "":
        raise ValueError(f"{name} is required.")
    return int(value)


def normalize_optional_int(value: Any, name: str) -> int | None:
    """Normalize one optional integer value."""
    if value is None or str(value).strip() == "":
        return None
    return normalize_int(value, name)


def normalize_float(value: Any, name: str) -> float:
    """Normalize one required float value."""
    if value is None or str(value).strip() == "":
        raise ValueError(f"{name} is required.")
    return float(value)


def normalize_required_string(value: Any, name: str) -> str:
    """Normalize one required non-empty string."""
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} is required.")
    return normalized


def normalize_input_path(parameters: dict[str, Any]) -> str:
    """Normalize one required `input_path` parameter."""
    return normalize_required_string(parameters.get("input_path"), "input_path")


def normalize_input_paths(parameters: dict[str, Any]) -> list[str]:
    """Normalize one required `input_paths` parameter."""
    raw_value = parameters.get("input_paths")
    if isinstance(raw_value, str):
        paths = [raw_value]
    elif isinstance(raw_value, (list, tuple)):
        paths = list(raw_value)
    else:
        raise ValueError("input_paths is required.")
    normalized = [str(path).strip() for path in paths if str(path).strip()]
    if not normalized:
        raise ValueError("input_paths is required.")
    return normalized
