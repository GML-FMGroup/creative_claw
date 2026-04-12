"""Declarative expert contracts used by the invoke_agent runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RequiredParameterGroup:
    """One required parameter rule where any listed key can satisfy the contract."""

    keys: tuple[str, ...]
    description: str


@dataclass(frozen=True, slots=True)
class ExpertSpec:
    """Static contract metadata for one expert agent."""

    name: str
    default_prompt_key: str = "prompt"
    supports_plain_prompt: bool = True
    default_parameters: dict[str, Any] = field(default_factory=dict)
    required_parameters: tuple[str, ...] = ()
    required_parameter_groups: tuple[RequiredParameterGroup, ...] = ()
    allowed_values: dict[str, tuple[str, ...]] = field(default_factory=dict)
    mirrored_output_keys: tuple[str, ...] = ()
    notes: str = ""


_DEFAULT_SPEC = ExpertSpec(name="default")

_EXPERT_SPECS = {
    "ImageGenerationAgent": ExpertSpec(
        name="ImageGenerationAgent",
        default_prompt_key="prompt",
        default_parameters={
            "provider": "nano_banana",
            "aspect_ratio": "16:9",
            "resolution": "1K",
            "size": "1024x1024",
            "quality": "high",
        },
        required_parameters=("prompt",),
        required_parameter_groups=(RequiredParameterGroup(keys=("prompt",), description="prompt"),),
        allowed_values={
            "provider": ("nano_banana", "seedream", "gpt_image"),
            "size": ("1024x1024", "1024x1536", "1536x1024"),
            "quality": ("low", "medium", "high"),
        },
        notes=(
            "Use prompt; optional provider, aspect_ratio, resolution. "
            "GPT Image is available through provider `gpt_image` and supports optional size and quality."
        ),
    ),
    "ImageEditingAgent": ExpertSpec(
        name="ImageEditingAgent",
        default_prompt_key="prompt",
        supports_plain_prompt=False,
        default_parameters={"provider": "nano_banana"},
        required_parameters=("prompt", "input_path or input_paths"),
        required_parameter_groups=(
            RequiredParameterGroup(keys=("prompt",), description="prompt"),
            RequiredParameterGroup(
                keys=("input_path", "input_paths"),
                description="input_path or input_paths",
            ),
        ),
        allowed_values={"provider": ("nano_banana", "seedream")},
        notes="Requires input image path plus editing prompt.",
    ),
    "ImageUnderstandingAgent": ExpertSpec(
        name="ImageUnderstandingAgent",
        default_prompt_key="mode",
        supports_plain_prompt=False,
        default_parameters={"mode": "description"},
        required_parameters=("input_path or input_paths", "mode"),
        required_parameter_groups=(
            RequiredParameterGroup(
                keys=("input_path", "input_paths"),
                description="input_path or input_paths",
            ),
            RequiredParameterGroup(keys=("mode",), description="mode"),
        ),
        allowed_values={"mode": ("description", "style", "ocr", "all")},
        mirrored_output_keys=("image_understanding_results",),
        notes="Requires image path; default mode is description.",
    ),
    "ImageToPromptAgent": ExpertSpec(
        name="ImageToPromptAgent",
        default_prompt_key="prompt",
        supports_plain_prompt=False,
        required_parameters=("input_path or input_paths",),
        required_parameter_groups=(
            RequiredParameterGroup(
                keys=("input_path", "input_paths"),
                description="input_path or input_paths",
            ),
        ),
        mirrored_output_keys=("image_to_prompt_results",),
        notes="Requires one or more image paths.",
    ),
    "ImageGroundingAgent": ExpertSpec(
        name="ImageGroundingAgent",
        default_prompt_key="prompt",
        supports_plain_prompt=False,
        required_parameters=("input_path", "prompt"),
        required_parameter_groups=(
            RequiredParameterGroup(keys=("input_path",), description="input_path"),
            RequiredParameterGroup(keys=("prompt",), description="prompt"),
        ),
        mirrored_output_keys=("image_ground_results",),
        notes="Requires one image path and one grounding prompt.",
    ),
    "ImageSegmentationAgent": ExpertSpec(
        name="ImageSegmentationAgent",
        default_prompt_key="prompt",
        supports_plain_prompt=False,
        default_parameters={"model": "DINO-X-1.0", "threshold": 0.25},
        required_parameters=("input_path", "prompt"),
        required_parameter_groups=(
            RequiredParameterGroup(keys=("input_path",), description="input_path"),
            RequiredParameterGroup(keys=("prompt",), description="prompt"),
        ),
        mirrored_output_keys=("image_segmentation_results",),
        notes=(
            "Requires one image path and one segmentation prompt; saves one binary mask file. "
            "Example invoke_agent JSON: "
            '{"input_path":"inbox/cli/demo.png","prompt":"person","threshold":0.2}. '
            "For chaining, read current_output.results[0].mask_path and pass that workspace path "
            "into a later expert or built-in tool."
        ),
    ),
    "KnowledgeAgent": ExpertSpec(
        name="KnowledgeAgent",
        default_prompt_key="prompt",
        required_parameters=("prompt",),
        required_parameter_groups=(RequiredParameterGroup(keys=("prompt",), description="prompt"),),
        notes="May also accept reference image paths.",
    ),
    "SearchAgent": ExpertSpec(
        name="SearchAgent",
        default_prompt_key="query",
        default_parameters={"mode": "all"},
        required_parameters=("query", "mode"),
        required_parameter_groups=(
            RequiredParameterGroup(keys=("query",), description="query"),
            RequiredParameterGroup(keys=("mode",), description="mode"),
        ),
        allowed_values={"mode": ("image", "text", "all")},
        notes="Default mode is all; optional count.",
    ),
    "VideoGenerationAgent": ExpertSpec(
        name="VideoGenerationAgent",
        default_prompt_key="prompt",
        default_parameters={
            "provider": "seedance",
            "mode": "prompt",
            "aspect_ratio": "16:9",
            "resolution": "720p",
        },
        required_parameters=("prompt or input_path/input_paths",),
        required_parameter_groups=(
            RequiredParameterGroup(
                keys=("prompt", "input_path", "input_paths"),
                description="prompt or input_path/input_paths",
            ),
        ),
        allowed_values={
            "provider": ("seedance", "veo"),
            "mode": (
                "prompt",
                "first_frame",
                "first_frame_and_last_frame",
                "reference_asset",
                "reference_style",
            ),
            "aspect_ratio": ("16:9", "9:16"),
            "resolution": ("720p", "1080p"),
        },
        notes="Use prompt-only or image-guided video generation with optional provider, mode, aspect_ratio, and resolution.",
    ),
}


def get_expert_spec(agent_name: str) -> ExpertSpec:
    """Return the declared contract for one expert."""
    return _EXPERT_SPECS.get(agent_name, _DEFAULT_SPEC)


def _has_parameter_value(value: Any) -> bool:
    """Return whether one parameter value should count as present."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(_has_parameter_value(item) for item in value)
    if isinstance(value, dict):
        return bool(value)
    return True


def _normalize_allowed_values(value: Any) -> list[str]:
    """Convert one scalar-or-list value into comparable lowercase strings."""
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = [value]
    return [str(item).strip().lower() for item in items if str(item).strip()]


def build_fallback_parameters(agent_name: str, prompt: str) -> dict[str, Any]:
    """Build fallback parameters from a plain-text invoke_agent prompt."""
    spec = get_expert_spec(agent_name)
    if not spec.supports_plain_prompt:
        required = ", ".join(spec.required_parameters) if spec.required_parameters else "structured parameters"
        raise ValueError(
            f"{agent_name} requires structured invoke_agent parameters. "
            f"Pass a JSON object string with: {required}."
        )
    parameters: dict[str, Any] = {spec.default_prompt_key: prompt}
    parameters.update(spec.default_parameters)
    return parameters


def validate_expert_parameters(agent_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Validate one normalized expert parameter payload against the declared contract."""
    spec = get_expert_spec(agent_name)
    missing_groups = [
        group.description
        for group in spec.required_parameter_groups
        if not any(_has_parameter_value(parameters.get(key)) for key in group.keys)
    ]
    if missing_groups:
        raise ValueError(
            f"{agent_name} requires structured invoke_agent parameters. "
            f"Missing: {', '.join(missing_groups)}."
        )

    for key, allowed in spec.allowed_values.items():
        if key not in parameters or parameters[key] is None:
            continue
        invalid_values = [
            value for value in _normalize_allowed_values(parameters[key]) if value not in allowed
        ]
        if invalid_values:
            raise ValueError(
                f"{agent_name} got invalid `{key}` value(s): {invalid_values}. "
                f"Allowed values: {list(allowed)}."
            )

    return parameters


def normalize_expert_output(
    agent_name: str,
    current_output: Any,
    forwarded_state_delta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize one expert output payload into the shared runtime contract."""
    if not isinstance(current_output, dict):
        return {
            "status": "error",
            "message": f"{agent_name} produced invalid current_output: expected a dict.",
            "output_files": [],
        }

    spec = get_expert_spec(agent_name)
    normalized = dict(current_output)
    normalized["status"] = str(normalized.get("status", "error")).strip().lower() or "error"
    normalized["message"] = (
        str(normalized.get("message", "")).strip() or f"{agent_name} finished without a message."
    )
    normalized["output_text"] = str(normalized.get("output_text", "") or "")
    output_files = normalized.get("output_files", [])
    normalized["output_files"] = output_files if isinstance(output_files, list) else []

    forwarded_state_delta = forwarded_state_delta or {}
    for key in spec.mirrored_output_keys:
        if key in forwarded_state_delta and key not in normalized:
            normalized[key] = forwarded_state_delta[key]

    return normalized


def build_expert_contract_summary() -> str:
    """Render concise expert parameter guidance for the orchestrator prompt."""
    lines = []
    for spec in _EXPERT_SPECS.values():
        required = ", ".join(spec.required_parameters) if spec.required_parameters else "none"
        defaults = (
            ", ".join(f"{key}={value}" for key, value in spec.default_parameters.items())
            if spec.default_parameters
            else "none"
        )
        lines.append(
            f"- {spec.name}: required={required}; fallback prompt key={spec.default_prompt_key}; plain_prompt={'yes' if spec.supports_plain_prompt else 'no'}; defaults={defaults}. {spec.notes}"
        )
    return "\n".join(lines)
