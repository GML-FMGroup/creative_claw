"""Shared provider capability metadata for video generation."""

from __future__ import annotations

from typing import Any

VIDEO_GENERATION_PROVIDERS = ("seedance", "veo", "kling")
VIDEO_GENERATION_DEFAULT_PROVIDER = "seedance"
VIDEO_GENERATION_DEFAULT_MODE = "prompt"
VIDEO_GENERATION_DEFAULT_ASPECT_RATIO = "16:9"
VIDEO_GENERATION_PERSON_GENERATION_VALUES = ("allow_all", "allow_adult")
VIDEO_GENERATION_KLING_MODE_VALUES = ("std", "pro")

_VIDEO_PROVIDER_CAPABILITIES = {
    "seedance": {
        "modes": (
            "prompt",
            "first_frame",
            "first_frame_and_last_frame",
            "reference_asset",
            "reference_style",
        ),
        "aspect_ratios": ("16:9", "9:16"),
        "resolutions": (),
        "default_resolution": "",
        "durations_by_mode": {},
        "default_duration_seconds": None,
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


def get_video_generation_default_parameters() -> dict[str, Any]:
    """Return provider-agnostic defaults for the video generation expert contract."""
    return {
        "provider": VIDEO_GENERATION_DEFAULT_PROVIDER,
        "mode": VIDEO_GENERATION_DEFAULT_MODE,
        "aspect_ratio": VIDEO_GENERATION_DEFAULT_ASPECT_RATIO,
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


def validate_video_generation_parameters(parameters: dict[str, Any]) -> None:
    """Validate one video generation payload using provider-specific capabilities."""
    provider = normalize_video_provider(parameters.get("provider"))
    mode = str(parameters.get("mode", VIDEO_GENERATION_DEFAULT_MODE) or "").strip().lower() or VIDEO_GENERATION_DEFAULT_MODE

    if mode not in get_supported_video_modes(provider):
        raise ValueError(
            f"VideoGenerationAgent provider `{provider}` does not support `mode={mode}`. "
            f"Allowed values: {list(get_supported_video_modes(provider))}."
        )

    if "aspect_ratio" in parameters and parameters.get("aspect_ratio") is not None:
        aspect_ratio = str(parameters.get("aspect_ratio") or "").strip()
        if aspect_ratio and aspect_ratio not in get_supported_video_aspect_ratios(provider):
            raise ValueError(
                f"VideoGenerationAgent provider `{provider}` does not support `aspect_ratio={aspect_ratio}`. "
                f"Allowed values: {list(get_supported_video_aspect_ratios(provider))}."
            )

    if "resolution" in parameters and parameters.get("resolution") is not None:
        resolution = str(parameters.get("resolution") or "").strip().lower()
        if resolution:
            supported_resolutions = get_supported_video_resolutions(provider)
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
            supported_durations = get_supported_video_durations(provider, mode=mode)
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

    if provider != "veo" and _has_non_empty_value(parameters.get("person_generation")):
        raise ValueError("VideoGenerationAgent parameter `person_generation` is supported only for provider `veo`.")

    if provider != "kling" and _has_non_empty_value(parameters.get("kling_mode")):
        raise ValueError("VideoGenerationAgent parameter `kling_mode` is supported only for provider `kling`.")

    if provider != "kling" and _has_non_empty_value(parameters.get("model_name")):
        raise ValueError("VideoGenerationAgent parameter `model_name` is supported only for provider `kling`.")


def build_video_generation_contract_notes() -> str:
    """Render one provider-aware contract summary for the orchestrator prompt."""
    provider_blocks = [
        (
            "provider `seedance`: "
            f"modes {list(get_supported_video_modes('seedance'))}, "
            f"aspect_ratio {list(get_supported_video_aspect_ratios('seedance'))}; "
            "do not pass resolution or duration_seconds."
        ),
        (
            "provider `veo`: "
            f"modes {list(get_supported_video_modes('veo'))}, "
            f"aspect_ratio {list(get_supported_video_aspect_ratios('veo'))}, "
            f"resolution {list(get_supported_video_resolutions('veo'))}, "
            f"duration_seconds {[str(value) for value in get_supported_video_durations('veo')]}."
        ),
        (
            "provider `kling`: "
            f"modes {list(get_supported_video_modes('kling'))}, "
            f"aspect_ratio {list(get_supported_video_aspect_ratios('kling'))}, "
            f"duration_seconds {[str(value) for value in get_supported_video_durations('kling')]}; "
            f"for mode `multi_reference`, allowed duration_seconds are "
            f"{[str(value) for value in get_supported_video_durations('kling', mode='multi_reference')]}."
        ),
    ]
    return (
        "Use prompt-only, image-guided, or video-extension generation with provider-aware parameters. "
        + " ".join(provider_blocks)
        + " Parameter `resolution` applies only to `veo`; `person_generation` applies only to `veo`; "
        + "`kling_mode` and `model_name` apply only to `kling`."
    )


def _has_non_empty_value(value: Any) -> bool:
    """Return whether one optional parameter should be treated as explicitly provided."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True
