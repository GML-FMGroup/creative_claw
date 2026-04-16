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
        allowed_values={"mode": ("description", "style", "ocr", "all", "prompt")},
        mirrored_output_keys=("image_understanding_results",),
        notes=(
            "Requires image path; default mode is description. "
            "Use mode `prompt` when the goal is reverse-prompt extraction or recreation guidance."
        ),
    ),
    "ImageBasicOperations": ExpertSpec(
        name="ImageBasicOperations",
        default_prompt_key="operation",
        supports_plain_prompt=False,
        required_parameters=("operation", "input_path or input_paths"),
        required_parameter_groups=(
            RequiredParameterGroup(keys=("operation",), description="operation"),
            RequiredParameterGroup(
                keys=("input_path", "input_paths"),
                description="input_path or input_paths",
            ),
        ),
        allowed_values={
            "operation": ("crop", "rotate", "flip", "info", "resize", "convert"),
            "direction": ("horizontal", "vertical"),
            "resample": ("nearest", "bilinear", "bicubic", "lanczos"),
            "output_format": ("png", "jpg", "jpeg", "webp"),
        },
        mirrored_output_keys=("image_basic_operation_results",),
        notes=(
            "Deterministic image operations only. "
            "Use operation plus operation-specific parameters such as crop box, degrees, direction, size, or output_format."
        ),
    ),
    "TextTransformExpert": ExpertSpec(
        name="TextTransformExpert",
        default_prompt_key="input_text",
        supports_plain_prompt=False,
        required_parameters=("input_text or text", "mode"),
        required_parameter_groups=(
            RequiredParameterGroup(keys=("input_text", "text"), description="input_text or text"),
            RequiredParameterGroup(keys=("mode",), description="mode"),
        ),
        allowed_values={
            "mode": ("rewrite", "expand", "compress", "translate", "structure", "title", "script"),
        },
        mirrored_output_keys=("text_transform_results",),
        notes=(
            "Atomic text transformation only. "
            "Optional parameters: target_language, style, constraints."
        ),
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
    "VideoUnderstandingExpert": ExpertSpec(
        name="VideoUnderstandingExpert",
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
        allowed_values={"mode": ("description", "shot_breakdown", "ocr", "prompt")},
        mirrored_output_keys=("video_understanding_results",),
        notes="Atomic video understanding only. Use mode prompt for prompt reverse engineering.",
    ),
    "VideoBasicOperations": ExpertSpec(
        name="VideoBasicOperations",
        default_prompt_key="operation",
        supports_plain_prompt=False,
        required_parameters=("operation", "input_path or input_paths"),
        required_parameter_groups=(
            RequiredParameterGroup(keys=("operation",), description="operation"),
            RequiredParameterGroup(
                keys=("input_path", "input_paths"),
                description="input_path or input_paths",
            ),
        ),
        allowed_values={
            "operation": ("info", "extract_frame", "trim", "concat", "convert"),
            "output_format": ("png", "jpg", "jpeg", "webp", "mp4", "mov", "mkv", "webm"),
        },
        mirrored_output_keys=("video_basic_operation_results",),
        notes=(
            "Deterministic video operations only. "
            "Use operation plus operation-specific parameters such as timestamp, start_time, end_time, duration, input_paths, or output_format."
        ),
    ),
    "AudioBasicOperations": ExpertSpec(
        name="AudioBasicOperations",
        default_prompt_key="operation",
        supports_plain_prompt=False,
        required_parameters=("operation", "input_path or input_paths"),
        required_parameter_groups=(
            RequiredParameterGroup(keys=("operation",), description="operation"),
            RequiredParameterGroup(
                keys=("input_path", "input_paths"),
                description="input_path or input_paths",
            ),
        ),
        allowed_values={
            "operation": ("info", "trim", "concat", "convert"),
            "output_format": ("mp3", "wav", "aac", "m4a", "flac", "ogg"),
        },
        mirrored_output_keys=("audio_basic_operation_results",),
        notes=(
            "Deterministic audio operations only. "
            "Use operation plus operation-specific parameters such as start_time, end_time, duration, sample_rate, bitrate, channels, or output_format."
        ),
    ),
    "SpeechRecognitionExpert": ExpertSpec(
        name="SpeechRecognitionExpert",
        default_prompt_key="input_path",
        supports_plain_prompt=False,
        default_parameters={"task": "auto"},
        required_parameters=("input_path or input_paths",),
        required_parameter_groups=(
            RequiredParameterGroup(
                keys=("input_path", "input_paths"),
                description="input_path or input_paths",
            ),
        ),
        allowed_values={
            "task": ("auto", "asr", "subtitle"),
            "subtitle_format": ("srt", "vtt"),
            "caption_type": ("auto", "speech", "singing"),
        },
        mirrored_output_keys=("speech_recognition_results", "speech_transcription_results"),
        notes=(
            "Speech recognition and subtitle generation for audio or video files. "
            "Optional parameters: task, language, timestamps, subtitle_format, output_path, "
            "subtitle_text/audio_text, caption_type, sta_punc_mode, words_per_line, max_lines."
        ),
    ),
    "SpeechTranscriptionExpert": ExpertSpec(
        name="SpeechTranscriptionExpert",
        default_prompt_key="input_path",
        supports_plain_prompt=False,
        default_parameters={"task": "auto"},
        required_parameters=("input_path or input_paths",),
        required_parameter_groups=(
            RequiredParameterGroup(
                keys=("input_path", "input_paths"),
                description="input_path or input_paths",
            ),
        ),
        allowed_values={
            "task": ("auto", "asr", "subtitle"),
            "subtitle_format": ("srt", "vtt"),
            "caption_type": ("auto", "speech", "singing"),
        },
        mirrored_output_keys=("speech_recognition_results", "speech_transcription_results"),
        notes=(
            "Compatibility alias for SpeechRecognitionExpert. "
            "Optional parameters: task, language, timestamps, subtitle_format, output_path, "
            "subtitle_text/audio_text, caption_type, sta_punc_mode, words_per_line, max_lines."
        ),
    ),
    "SpeechSynthesisExpert": ExpertSpec(
        name="SpeechSynthesisExpert",
        default_prompt_key="text",
        supports_plain_prompt=True,
        required_parameters=("text or ssml",),
        required_parameter_groups=(
            RequiredParameterGroup(keys=("text", "ssml"), description="text or ssml"),
        ),
        allowed_values={"audio_format": ("mp3", "wav", "flac", "pcm")},
        mirrored_output_keys=("speech_synthesis_results",),
        notes=(
            "Text-to-speech only. "
            "Uses the ByteDance HTTP streaming TTS path. Optional parameters: speaker, resource_id, audio_format, sample_rate, language, enable_timestamp, latex_parser."
        ),
    ),
    "MusicGenerationExpert": ExpertSpec(
        name="MusicGenerationExpert",
        default_prompt_key="prompt",
        supports_plain_prompt=True,
        default_parameters={"instrumental": True},
        required_parameters=("prompt",),
        required_parameter_groups=(RequiredParameterGroup(keys=("prompt",), description="prompt"),),
        allowed_values={"audio_format": ("mp3", "wav", "flac")},
        mirrored_output_keys=("music_generation_results",),
        notes=(
            "Generate a music or BGM clip from text instructions. "
            "Optional parameters: lyrics, instrumental, audio_format, sample_rate, bitrate, model."
        ),
    ),
    "3DGeneration": ExpertSpec(
        name="3DGeneration",
        default_prompt_key="prompt",
        default_parameters={
            "provider": "hy3d",
            "model": "3.0",
            "generate_type": "normal",
            "enable_pbr": False,
            "timeout_seconds": 900,
            "interval_seconds": 8,
        },
        required_parameters=("prompt or input_path/input_paths",),
        required_parameter_groups=(
            RequiredParameterGroup(
                keys=("prompt", "input_path", "input_paths"),
                description="prompt or input_path/input_paths",
            ),
        ),
        allowed_values={
            "provider": ("hy3d",),
            "model": ("3.0", "3.1"),
            "generate_type": ("normal", "lowpoly", "sketch", "geometry"),
            "result_format": ("stl", "usdz", "fbx"),
        },
        mirrored_output_keys=("three_d_generation_results",),
        notes=(
            "Generates 3D assets through Tencent Cloud Hunyuan 3D Pro. "
            "V1 supports prompt-only, image-only, and Sketch prompt-plus-image input."
        ),
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
