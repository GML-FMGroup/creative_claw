"""Shared provider capability metadata for video generation."""

from __future__ import annotations

from typing import Any

VIDEO_GENERATION_PROVIDERS = ("seedance", "veo", "kling")
VIDEO_GENERATION_DEFAULT_PROVIDER = "seedance"
VIDEO_GENERATION_DEFAULT_MODE = "prompt"
VIDEO_GENERATION_DEFAULT_ASPECT_RATIO = "16:9"
VIDEO_GENERATION_PROMPT_REWRITE_VALUES = ("auto", "off")
VIDEO_GENERATION_PERSON_GENERATION_VALUES = ("allow_all", "allow_adult")
VIDEO_GENERATION_KLING_MODE_VALUES = ("std", "pro")
VIDEO_GENERATION_SEEDANCE_LEGACY_MODEL_NAME = "doubao-seedance-1-0-pro-250528"
VIDEO_GENERATION_SEEDANCE_2_MODEL_NAME = "doubao-seedance-2-0-260128"
VIDEO_GENERATION_SEEDANCE_2_FAST_MODEL_NAME = "doubao-seedance-2-0-fast-260128"
VIDEO_GENERATION_SEEDANCE_MODEL_NAME = VIDEO_GENERATION_SEEDANCE_2_MODEL_NAME
VIDEO_GENERATION_SEEDANCE_MODEL_NAMES = (
    VIDEO_GENERATION_SEEDANCE_MODEL_NAME,
    VIDEO_GENERATION_SEEDANCE_2_FAST_MODEL_NAME,
    VIDEO_GENERATION_SEEDANCE_LEGACY_MODEL_NAME,
)
VIDEO_GENERATION_VEO_MODEL_NAME = "veo-3.1-generate-preview"
VIDEO_GENERATION_KLING_MODEL_NAME = "kling-v3"
VIDEO_GENERATION_KLING_MULTI_REFERENCE_MODEL_NAME = "kling-v1-6"

_VIDEO_PROVIDER_MODEL_CAPABILITIES = {
    "seedance": {
        "model_name": VIDEO_GENERATION_SEEDANCE_MODEL_NAME,
        "native_audio_output": "supported",
        "subtitle_file_output": "not_supported",
        "summary": (
            "Seedance 2.0 can generate synchronized audio from prompt cues, including "
            "dialogue, sound effects, and background music; it does not return subtitle/SRT files."
        ),
    },
    "veo": {
        "model_name": VIDEO_GENERATION_VEO_MODEL_NAME,
        "native_audio_output": "supported",
        "subtitle_file_output": "not_supported",
        "summary": (
            "Supports native synchronized audio from prompt cues such as dialogue, "
            "ambience, music, and sound effects; it does not return subtitle/SRT files."
        ),
    },
    "kling": {
        "model_name": VIDEO_GENERATION_KLING_MODEL_NAME,
        "native_audio_output": "not_exposed",
        "subtitle_file_output": "not_supported",
        "summary": (
            "Current Creative Claw Kling integration does not expose native audio "
            "controls, so treat it as visual-only and do not promise subtitles."
        ),
    },
}

_VIDEO_PROVIDER_CAPABILITIES = {
    "seedance": {
        "modes": (
            "prompt",
            "first_frame",
            "first_frame_and_last_frame",
            "reference_asset",
            "reference_style",
        ),
        "aspect_ratios": ("16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive"),
        "resolutions": ("480p", "720p", "1080p"),
        "default_resolution": "720p",
        "durations_by_mode": {
            "*": tuple([-1, *range(4, 16)]),
        },
        "default_duration_seconds": 5,
    },
    "veo": {
        "modes": (
            "prompt",
            "first_frame",
            "first_frame_and_last_frame",
            "reference_asset",
            "reference_style",
            "video_extension",
        ),
        "aspect_ratios": ("16:9", "9:16"),
        "resolutions": ("720p", "1080p", "4k"),
        "default_resolution": "720p",
        "durations_by_mode": {
            "*": (4, 6, 8),
        },
        "default_duration_seconds": 8,
    },
    "kling": {
        "modes": (
            "prompt",
            "first_frame",
            "first_frame_and_last_frame",
            "multi_reference",
        ),
        "aspect_ratios": ("16:9", "9:16", "1:1"),
        "resolutions": (),
        "default_resolution": "",
        "durations_by_mode": {
            "*": tuple(range(3, 16)),
            "multi_reference": (5, 10),
        },
        "default_duration_seconds": 5,
    },
}

VIDEO_GENERATION_MODES = tuple(
    dict.fromkeys(
        mode
        for provider in VIDEO_GENERATION_PROVIDERS
        for mode in _VIDEO_PROVIDER_CAPABILITIES[provider]["modes"]
    )
)
VIDEO_GENERATION_ASPECT_RATIOS = tuple(
    dict.fromkeys(
        aspect_ratio
        for provider in VIDEO_GENERATION_PROVIDERS
        for aspect_ratio in _VIDEO_PROVIDER_CAPABILITIES[provider]["aspect_ratios"]
    )
)
VIDEO_GENERATION_RESOLUTIONS = tuple(
    dict.fromkeys(
        resolution
        for provider in VIDEO_GENERATION_PROVIDERS
        for resolution in _VIDEO_PROVIDER_CAPABILITIES[provider]["resolutions"]
    )
)


def normalize_video_provider(raw_value: Any) -> str:
    """Return one supported video provider or the default provider."""
    value = str(raw_value or "").strip().lower()
    return value if value in VIDEO_GENERATION_PROVIDERS else VIDEO_GENERATION_DEFAULT_PROVIDER


def normalize_seedance_model_name(raw_value: Any) -> str:
    """Return one supported Seedance model id or the default Seedance 2.0 model."""
    value = str(raw_value or "").strip()
    return value if value in VIDEO_GENERATION_SEEDANCE_MODEL_NAMES else VIDEO_GENERATION_SEEDANCE_MODEL_NAME


def seedance_model_supports_audio(model_name: str) -> bool:
    """Return whether one Seedance model supports native generated audio."""
    return normalize_seedance_model_name(model_name) in {
        VIDEO_GENERATION_SEEDANCE_MODEL_NAME,
        VIDEO_GENERATION_SEEDANCE_2_FAST_MODEL_NAME,
    }


def get_supported_seedance_resolutions(model_name: str) -> tuple[str, ...]:
    """Return supported Seedance resolutions for one model id."""
    normalized_model = normalize_seedance_model_name(model_name)
    if normalized_model == VIDEO_GENERATION_SEEDANCE_2_FAST_MODEL_NAME:
        return ("480p", "720p")
    return ("480p", "720p", "1080p")


def get_supported_seedance_durations(model_name: str) -> tuple[int, ...]:
    """Return supported integer durations for one Seedance model id."""
    normalized_model = normalize_seedance_model_name(model_name)
    if normalized_model in {
        VIDEO_GENERATION_SEEDANCE_MODEL_NAME,
        VIDEO_GENERATION_SEEDANCE_2_FAST_MODEL_NAME,
    }:
        return tuple([-1, *range(4, 16)])
    return tuple(range(2, 13))


def get_video_generation_default_parameters() -> dict[str, Any]:
    """Return provider-agnostic defaults for the video generation expert contract."""
    return {
        "prompt_rewrite": "auto",
        "provider": VIDEO_GENERATION_DEFAULT_PROVIDER,
        "mode": VIDEO_GENERATION_DEFAULT_MODE,
        "aspect_ratio": VIDEO_GENERATION_DEFAULT_ASPECT_RATIO,
    }


def get_video_generation_model_name(
    provider: str,
    *,
    mode: str = VIDEO_GENERATION_DEFAULT_MODE,
) -> str:
    """Return the effective model name for one provider and generation mode."""
    current_provider = normalize_video_provider(provider)
    current_mode = str(mode or VIDEO_GENERATION_DEFAULT_MODE).strip().lower()
    if current_provider == "kling" and current_mode == "multi_reference":
        return VIDEO_GENERATION_KLING_MULTI_REFERENCE_MODEL_NAME
    return str(_VIDEO_PROVIDER_MODEL_CAPABILITIES[current_provider]["model_name"])


def get_video_generation_model_capabilities(provider: str) -> dict[str, str]:
    """Return model-level audio and subtitle capability metadata for one provider."""
    current_provider = normalize_video_provider(provider)
    return {
        key: str(value)
        for key, value in _VIDEO_PROVIDER_MODEL_CAPABILITIES[current_provider].items()
    }


def get_supported_video_modes(provider: str) -> tuple[str, ...]:
    """Return the supported generation modes for one provider."""
    current_provider = normalize_video_provider(provider)
    return tuple(_VIDEO_PROVIDER_CAPABILITIES[current_provider]["modes"])


def get_supported_video_aspect_ratios(provider: str) -> tuple[str, ...]:
    """Return the supported aspect ratios for one provider."""
    current_provider = normalize_video_provider(provider)
    return tuple(_VIDEO_PROVIDER_CAPABILITIES[current_provider]["aspect_ratios"])


def get_supported_video_resolutions(provider: str) -> tuple[str, ...]:
    """Return the supported output resolutions for one provider."""
    current_provider = normalize_video_provider(provider)
    return tuple(_VIDEO_PROVIDER_CAPABILITIES[current_provider]["resolutions"])


def get_default_video_resolution(provider: str) -> str:
    """Return the default output resolution for one provider, if any."""
    current_provider = normalize_video_provider(provider)
    return str(_VIDEO_PROVIDER_CAPABILITIES[current_provider]["default_resolution"])


def get_supported_video_durations(provider: str, *, mode: str = VIDEO_GENERATION_DEFAULT_MODE) -> tuple[int, ...]:
    """Return the supported duration values for one provider and mode."""
    current_provider = normalize_video_provider(provider)
    provider_capabilities = _VIDEO_PROVIDER_CAPABILITIES[current_provider]
    durations_by_mode: dict[str, tuple[int, ...]] = provider_capabilities["durations_by_mode"]  # type: ignore[assignment]
    normalized_mode = str(mode or VIDEO_GENERATION_DEFAULT_MODE).strip().lower() or VIDEO_GENERATION_DEFAULT_MODE
    return tuple(durations_by_mode.get(normalized_mode, durations_by_mode.get("*", ())))


def get_default_video_duration(provider: str) -> int | None:
    """Return the default duration for one provider when applicable."""
    current_provider = normalize_video_provider(provider)
    return _VIDEO_PROVIDER_CAPABILITIES[current_provider]["default_duration_seconds"]  # type: ignore[return-value]


def normalize_provider_video_mode(provider: str, raw_value: Any) -> str:
    """Return one supported mode for the given provider."""
    supported_modes = get_supported_video_modes(provider)
    value = str(raw_value or "").strip().lower()
    return value if value in supported_modes else VIDEO_GENERATION_DEFAULT_MODE


def normalize_provider_video_aspect_ratio(provider: str, raw_value: Any) -> str:
    """Return one supported aspect ratio for the given provider."""
    supported_aspect_ratios = get_supported_video_aspect_ratios(provider)
    value = str(raw_value or "").strip()
    return value if value in supported_aspect_ratios else VIDEO_GENERATION_DEFAULT_ASPECT_RATIO


def normalize_provider_video_resolution(provider: str, raw_value: Any) -> str:
    """Return one supported output resolution for the given provider."""
    supported_resolutions = get_supported_video_resolutions(provider)
    default_resolution = get_default_video_resolution(provider)
    value = str(raw_value or "").strip().lower()
    if not supported_resolutions:
        return ""
    return value if value in supported_resolutions else default_resolution


def normalize_seedance_video_resolution(model_name: str, raw_value: Any) -> str:
    """Return one supported Seedance resolution for the selected model."""
    supported_resolutions = get_supported_seedance_resolutions(model_name)
    value = str(raw_value or "").strip().lower()
    return value if value in supported_resolutions else "720p"


def normalize_seedance_video_duration(model_name: str, raw_value: Any) -> int:
    """Return one supported Seedance duration for the selected model."""
    supported_durations = get_supported_seedance_durations(model_name)
    default_duration = 5 if seedance_model_supports_audio(model_name) else 8
    if raw_value is None or str(raw_value).strip() == "":
        return default_duration
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        return default_duration
    return value if value in supported_durations else default_duration


def normalize_provider_video_duration(
    provider: str,
    raw_value: Any,
    *,
    mode: str = VIDEO_GENERATION_DEFAULT_MODE,
) -> int | None:
    """Return one supported duration for the given provider and mode."""
    supported_durations = get_supported_video_durations(provider, mode=mode)
    default_duration = get_default_video_duration(provider)
    if not supported_durations:
        return default_duration
    if raw_value is None or str(raw_value).strip() == "":
        return default_duration
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError):
        return default_duration
    return value if value in supported_durations else default_duration


def normalize_video_prompt_rewrite(raw_value: Any) -> str:
    """Return one supported agent-side prompt rewrite mode."""
    if raw_value is None:
        return "auto"
    value = str(raw_value).strip().lower()
    if not value:
        return "auto"
    if value not in VIDEO_GENERATION_PROMPT_REWRITE_VALUES:
        raise ValueError(
            "prompt_rewrite must be one of: "
            f"{sorted(VIDEO_GENERATION_PROMPT_REWRITE_VALUES)}."
        )
    return value


def validate_video_generation_parameters(parameters: dict[str, Any]) -> None:
    """Validate one video generation payload using provider-specific capabilities."""
    provider = normalize_video_provider(parameters.get("provider"))
    mode = str(parameters.get("mode", VIDEO_GENERATION_DEFAULT_MODE) or "").strip().lower() or VIDEO_GENERATION_DEFAULT_MODE

    if mode not in get_supported_video_modes(provider):
        raise ValueError(
            f"VideoGenerationAgent provider `{provider}` does not support `mode={mode}`. "
            f"Allowed values: {list(get_supported_video_modes(provider))}."
        )

    if "prompt_rewrite" in parameters:
        normalize_video_prompt_rewrite(parameters.get("prompt_rewrite"))

    if "aspect_ratio" in parameters and parameters.get("aspect_ratio") is not None:
        aspect_ratio = str(parameters.get("aspect_ratio") or "").strip()
        if aspect_ratio and aspect_ratio not in get_supported_video_aspect_ratios(provider):
            raise ValueError(
                f"VideoGenerationAgent provider `{provider}` does not support `aspect_ratio={aspect_ratio}`. "
                f"Allowed values: {list(get_supported_video_aspect_ratios(provider))}."
            )

    seedance_model_name = normalize_seedance_model_name(parameters.get("model_name")) if provider == "seedance" else ""

    if "model_name" in parameters and _has_non_empty_value(parameters.get("model_name")):
        model_name = str(parameters.get("model_name") or "").strip()
        if provider == "seedance":
            if model_name not in VIDEO_GENERATION_SEEDANCE_MODEL_NAMES:
                raise ValueError(
                    "VideoGenerationAgent provider `seedance` does not support "
                    f"`model_name={model_name}`. Allowed values: {list(VIDEO_GENERATION_SEEDANCE_MODEL_NAMES)}."
                )
        elif provider != "kling":
            raise ValueError("VideoGenerationAgent parameter `model_name` is supported only for provider `seedance` or `kling`.")

    if "resolution" in parameters and parameters.get("resolution") is not None:
        resolution = str(parameters.get("resolution") or "").strip().lower()
        if resolution:
            supported_resolutions = (
                get_supported_seedance_resolutions(seedance_model_name)
                if provider == "seedance"
                else get_supported_video_resolutions(provider)
            )
            if not supported_resolutions:
                raise ValueError(
                    f"VideoGenerationAgent parameter `resolution` is not supported for provider `{provider}`."
                )
            if resolution not in supported_resolutions:
                raise ValueError(
                    f"VideoGenerationAgent provider `{provider}` does not support `resolution={resolution}`. "
                    f"Allowed values: {list(supported_resolutions)}."
                )

    if "duration_seconds" in parameters and parameters.get("duration_seconds") is not None:
        raw_duration = str(parameters.get("duration_seconds") or "").strip()
        if raw_duration:
            supported_durations = (
                get_supported_seedance_durations(seedance_model_name)
                if provider == "seedance"
                else get_supported_video_durations(provider, mode=mode)
            )
            if not supported_durations:
                raise ValueError(
                    f"VideoGenerationAgent parameter `duration_seconds` is not supported for provider `{provider}`."
                )
            try:
                duration_value = int(raw_duration)
            except ValueError as exc:
                raise ValueError("VideoGenerationAgent parameter `duration_seconds` must be an integer.") from exc
            if duration_value not in supported_durations:
                raise ValueError(
                    f"VideoGenerationAgent provider `{provider}` does not support `duration_seconds={duration_value}` "
                    f"for mode `{mode}`. Allowed values: {[str(value) for value in supported_durations]}."
                )

    if provider == "seedance" and _has_non_empty_value(parameters.get("generate_audio")):
        if str(parameters.get("generate_audio")).strip().lower() in {"1", "true", "yes", "y", "on"}:
            if not seedance_model_supports_audio(seedance_model_name):
                raise ValueError(
                    "VideoGenerationAgent provider `seedance` supports `generate_audio=true` "
                    "only for Seedance 2.0 or Seedance 2.0 fast."
                )

    if provider != "veo" and _has_non_empty_value(parameters.get("person_generation")):
        raise ValueError("VideoGenerationAgent parameter `person_generation` is supported only for provider `veo`.")

    if provider != "kling" and _has_non_empty_value(parameters.get("kling_mode")):
        raise ValueError("VideoGenerationAgent parameter `kling_mode` is supported only for provider `kling`.")


def build_video_generation_contract_notes() -> str:
    """Render one provider-aware contract summary for the orchestrator prompt."""
    seedance_model_capabilities = get_video_generation_model_capabilities("seedance")
    veo_model_capabilities = get_video_generation_model_capabilities("veo")
    kling_model_capabilities = get_video_generation_model_capabilities("kling")
    provider_blocks = [
        (
            "provider `seedance` "
            f"(model `{get_video_generation_model_name('seedance')}`): "
            f"modes {list(get_supported_video_modes('seedance'))}, "
            f"aspect_ratio {list(get_supported_video_aspect_ratios('seedance'))}, "
            f"resolution {list(get_supported_video_resolutions('seedance'))}, "
            f"duration_seconds {[str(value) for value in get_supported_video_durations('seedance')]}; "
            f"{seedance_model_capabilities['summary']}; "
            f"use `model_name={VIDEO_GENERATION_SEEDANCE_2_FAST_MODEL_NAME}` for faster generation; "
            "set `generate_audio=true` for native dialogue/audio."
        ),
        (
            "provider `veo` "
            f"(model `{get_video_generation_model_name('veo')}`): "
            f"modes {list(get_supported_video_modes('veo'))}, "
            f"aspect_ratio {list(get_supported_video_aspect_ratios('veo'))}, "
            f"resolution {list(get_supported_video_resolutions('veo'))}, "
            f"duration_seconds {[str(value) for value in get_supported_video_durations('veo')]}; "
            f"{veo_model_capabilities['summary']} Do not pass separate audio files."
        ),
        (
            "provider `kling` "
            f"(basic model `{get_video_generation_model_name('kling')}`): "
            f"modes {list(get_supported_video_modes('kling'))}, "
            f"aspect_ratio {list(get_supported_video_aspect_ratios('kling'))}, "
            f"duration_seconds {[str(value) for value in get_supported_video_durations('kling')]}; "
            f"{kling_model_capabilities['summary']}; "
            f"for mode `multi_reference`, allowed duration_seconds are "
            f"{[str(value) for value in get_supported_video_durations('kling', mode='multi_reference')]} "
            f"and the effective model is `{get_video_generation_model_name('kling', mode='multi_reference')}`."
        ),
    ]
    return (
        "Use prompt-only, image-guided, or video-extension generation with provider-aware parameters. "
        + " ".join(provider_blocks)
        + " Agent-only parameter `prompt_rewrite` accepts `auto` or `off` and controls local prompt rewriting. "
        + "Parameter `person_generation` applies only to `veo`; "
        + "`kling_mode` applies only to `kling`; `model_name` applies to `seedance` or `kling`."
    )


def build_video_generation_routing_notes() -> str:
    """Render concise video-generation routing guidance for the main orchestrator."""
    return "\n".join(
        [
            (
                "- For video with native audio, dialogue, ambience, music, or sound effects, "
                "prefer `VideoGenerationAgent` provider `seedance` with Seedance 2.0 or Seedance 2.0 fast "
                "and `generate_audio=true`; keep exact dialogue in quoted text and use `prompt_rewrite=off`. "
                "Veo can also be used when the user explicitly asks for Veo."
            ),
            (
                "- For subtitle files, captions, SRT/VTT, or transcripts, do not rely on video "
                "generation models to produce structured subtitles; generate or obtain the video "
                "first, then use `SpeechRecognitionExpert`."
            ),
            (
                "- `VideoGenerationAgent` provider `seedance` defaults to "
                f"`{get_video_generation_model_name('seedance')}` and supports native generated audio "
                "through `generate_audio=true`; it does not return structured subtitle files."
            ),
            (
                "- Treat current `VideoGenerationAgent` provider `kling` integration as visual-only "
                "for audio/subtitle routing because native audio controls are not exposed; use "
                f"`model_name={get_video_generation_model_name('kling')}` for basic Kling routes and "
                f"`model_name={get_video_generation_model_name('kling', mode='multi_reference')}` "
                "for `multi_reference`."
            ),
        ]
    )


def _has_non_empty_value(value: Any) -> bool:
    """Return whether one optional parameter should be treated as explicitly provided."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True
