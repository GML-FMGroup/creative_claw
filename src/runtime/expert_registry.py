"""Declarative expert contracts used by the invoke_agent runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ExpertSpec:
    """Static contract metadata for one expert agent."""

    name: str
    default_prompt_key: str = "prompt"
    supports_plain_prompt: bool = True
    default_parameters: dict[str, Any] = field(default_factory=dict)
    required_parameters: tuple[str, ...] = ()
    notes: str = ""


_DEFAULT_SPEC = ExpertSpec(name="default")

_EXPERT_SPECS = {
    "ImageGenerationAgent": ExpertSpec(
        name="ImageGenerationAgent",
        default_prompt_key="prompt",
        default_parameters={"provider": "nano_banana", "aspect_ratio": "16:9", "resolution": "1K"},
        required_parameters=("prompt",),
        notes="Use prompt; optional provider, aspect_ratio, resolution.",
    ),
    "ImageEditingAgent": ExpertSpec(
        name="ImageEditingAgent",
        default_prompt_key="prompt",
        supports_plain_prompt=False,
        default_parameters={"provider": "nano_banana"},
        required_parameters=("prompt", "input_path or input_paths"),
        notes="Requires input image path plus editing prompt.",
    ),
    "ImageUnderstandingAgent": ExpertSpec(
        name="ImageUnderstandingAgent",
        default_prompt_key="mode",
        supports_plain_prompt=False,
        default_parameters={"mode": "description"},
        required_parameters=("input_path or input_paths", "mode"),
        notes="Requires image path; default mode is description.",
    ),
    "ImageToPromptAgent": ExpertSpec(
        name="ImageToPromptAgent",
        default_prompt_key="prompt",
        supports_plain_prompt=False,
        required_parameters=("input_path or input_paths",),
        notes="Requires one or more image paths.",
    ),
    "ImageGroundingAgent": ExpertSpec(
        name="ImageGroundingAgent",
        default_prompt_key="prompt",
        supports_plain_prompt=False,
        required_parameters=("input_path", "prompt"),
        notes="Requires one image path and one grounding prompt.",
    ),
    "KnowledgeAgent": ExpertSpec(
        name="KnowledgeAgent",
        default_prompt_key="prompt",
        required_parameters=("prompt",),
        notes="May also accept reference image paths.",
    ),
    "SearchAgent": ExpertSpec(
        name="SearchAgent",
        default_prompt_key="query",
        default_parameters={"mode": "all"},
        required_parameters=("query", "mode"),
        notes="Default mode is all; optional count.",
    ),
}


def get_expert_spec(agent_name: str) -> ExpertSpec:
    """Return the declared contract for one expert."""
    return _EXPERT_SPECS.get(agent_name, _DEFAULT_SPEC)


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
