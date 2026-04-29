"""Video generation expert for Creative Claw."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator
from typing_extensions import override

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from src.agents.experts.base import CreativeExpert
from src.agents.experts.video_generation.capabilities import (
    get_default_video_duration,
    get_default_video_resolution,
    normalize_provider_video_aspect_ratio,
    normalize_provider_video_duration,
    normalize_provider_video_mode,
    normalize_provider_video_resolution,
    normalize_seedance_model_name,
    normalize_seedance_video_duration,
    normalize_seedance_video_resolution,
    normalize_video_prompt_rewrite,
    normalize_video_provider,
)
from src.agents.experts.video_generation import tool as video_tools
from src.logger import logger
from src.runtime.workspace import build_workspace_file_record, save_binary_output


async def _prepare_prompts(
    ctx: InvocationContext,
    prompt_list: list[str],
    *,
    prompt_rewrite: str,
) -> list[str]:
    """Return prompts after applying the agent-side rewrite policy."""
    if prompt_rewrite == "off":
        return prompt_list

    enhanced_prompt_results = await asyncio.gather(
        *[
            video_tools.prompt_enhancement_tool(ctx, prompt)
            if prompt
            else asyncio.sleep(0, result={"status": "success", "message": ""})
            for prompt in prompt_list
        ]
    )
    normalized_prompts: list[str] = []
    for original_prompt, result in zip(prompt_list, enhanced_prompt_results):
        if result["status"] == "success":
            normalized_prompts.append(str(result["message"]).strip())
        else:
            logger.warning("VideoGenerationAgent: prompt enhancement failed, using original prompt")
            normalized_prompts.append(original_prompt)
    return normalized_prompts


def _parse_optional_bool(value) -> bool | None:
    """Return a bool for explicit values and None when the parameter is absent."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_bool(value, *, default: bool = False) -> bool:
    """Return a bool for flexible user-provided values."""
    parsed = _parse_optional_bool(value)
    return default if parsed is None else parsed


class VideoGenerationAgent(CreativeExpert):
    """Generate one or more videos from prompt, image, or video-guided inputs."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the video generation expert."""
        super().__init__(name=name, sub_agents=[], description=description)

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run the video expert with the normalized session parameters."""
        current_parameters = ctx.session.state.get("current_parameters", {})
        raw_prompt = current_parameters.get("prompt", "")
        prompt_list = raw_prompt if isinstance(raw_prompt, list) else [raw_prompt]
        prompt_list = [str(prompt).strip() for prompt in prompt_list]
        input_paths = current_parameters.get("input_paths", current_parameters.get("input_path", []))
        if isinstance(input_paths, str):
            input_paths = [input_paths]
        input_paths = [str(path).strip() for path in input_paths if str(path).strip()]

        provider = normalize_video_provider(current_parameters.get("provider"))
        mode = normalize_provider_video_mode(provider, current_parameters.get("mode", "prompt"))
        seedance_model_name = normalize_seedance_model_name(current_parameters.get("model_name"))
        if provider == "kling":
            aspect_ratio = normalize_provider_video_aspect_ratio(
                provider,
                current_parameters.get("aspect_ratio", "16:9"),
            )
            resolution = ""
            duration_seconds = int(
                normalize_provider_video_duration(
                    provider,
                    current_parameters.get("duration_seconds", get_default_video_duration(provider) or 5),
                    mode=mode,
                )
                or get_default_video_duration(provider)
                or 5
            )
        elif provider == "seedance":
            aspect_ratio = normalize_provider_video_aspect_ratio(
                provider,
                current_parameters.get("aspect_ratio", "16:9"),
            )
            resolution = normalize_seedance_video_resolution(
                seedance_model_name,
                current_parameters.get("resolution", ""),
            )
            duration_seconds = normalize_seedance_video_duration(
                seedance_model_name,
                current_parameters.get("duration_seconds"),
            )
        else:
            aspect_ratio = normalize_provider_video_aspect_ratio(
                provider,
                current_parameters.get("aspect_ratio", "16:9"),
            )
            resolution = normalize_provider_video_resolution(
                provider,
                current_parameters.get("resolution", get_default_video_resolution(provider)),
            )
            duration_seconds = int(
                normalize_provider_video_duration(
                    provider,
                    current_parameters.get("duration_seconds", get_default_video_duration(provider) or 8),
                    mode=mode,
                )
                or get_default_video_duration(provider)
                or 8
            )
        negative_prompt = str(current_parameters.get("negative_prompt", "") or "").strip()
        kling_model_name = str(current_parameters.get("model_name", "") or "").strip()
        kling_mode = video_tools.normalize_kling_mode(current_parameters.get("kling_mode", "std"))
        seedance_generate_audio = _parse_optional_bool(current_parameters.get("generate_audio"))
        seedance_watermark = _parse_bool(current_parameters.get("watermark"), default=False)

        if not any(prompt_list) and not input_paths:
            error_text = f"Missing parameters provided to {self.name}, must include prompt or input_path/input_paths."
            current_output = {"status": "error", "message": error_text}
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": current_output})
            return

        if mode != "prompt" and not input_paths:
            error_text = f"{self.name} requires input_path or input_paths when mode is {mode}."
            current_output = {"status": "error", "message": error_text}
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": current_output})
            return

        try:
            prompt_rewrite = normalize_video_prompt_rewrite(current_parameters.get("prompt_rewrite"))
            person_generation = video_tools.normalize_person_generation(
                current_parameters.get("person_generation")
            )
            seed = (
                video_tools.normalize_seedance_seed(current_parameters.get("seed"))
                if provider == "seedance"
                else video_tools.normalize_video_seed(current_parameters.get("seed"))
            )
            video_tools._validate_mode_input_paths(provider, mode, input_paths)
        except ValueError as exc:
            error_text = f"{self.name} got invalid parameters: {exc}"
            current_output = {"status": "error", "message": error_text}
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": current_output})
            return

        normalized_prompts = await _prepare_prompts(
            ctx,
            prompt_list,
            prompt_rewrite=prompt_rewrite,
        )

        if provider == "veo":
            generation_tasks = [
                video_tools.veo_video_generation_tool(
                    prompt,
                    input_paths=input_paths,
                    mode=mode,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    duration_seconds=duration_seconds,
                    negative_prompt=negative_prompt,
                    person_generation=person_generation,
                    seed=seed,
                )
                for prompt in normalized_prompts
            ]
        elif provider == "kling":
            generation_tasks = [
                video_tools.kling_video_generation_tool(
                    prompt,
                    input_paths=input_paths,
                    mode=mode,
                    aspect_ratio=aspect_ratio,
                    duration_seconds=duration_seconds,
                    negative_prompt=negative_prompt,
                    model_name=kling_model_name,
                    kling_mode=kling_mode,
                )
                for prompt in normalized_prompts
            ]
        else:
            generation_tasks = [
                video_tools.seedance_video_generation_tool(
                    prompt,
                    input_paths=input_paths,
                    mode=mode,
                    aspect_ratio=aspect_ratio,
                    model_name=seedance_model_name,
                    resolution=resolution,
                    duration_seconds=duration_seconds,
                    generate_audio=seedance_generate_audio,
                    watermark=seedance_watermark,
                    seed=seed,
                )
                for prompt in normalized_prompts
            ]
        result_list = await asyncio.gather(*generation_tasks)

        current_turn = int(ctx.session.state.get("turn_index", 0) or 0)
        current_step = int(ctx.session.state.get("step", 0) or 0)
        current_expert_step = int(ctx.session.state.get("expert_step", 0) or 0)
        output_files = []
        messages: list[str] = []
        for index, (prompt, result) in enumerate(zip(normalized_prompts, result_list)):
            if result["status"] == "error":
                messages.append(f"video task {index + 1} failed: {result['message']}")
                continue

            output_path = save_binary_output(
                result["message"],
                session_id=ctx.session.id,
                turn_index=current_turn,
                step=current_step,
                output_type="video_generation",
                index=index,
                extension=".mp4",
            )
            artifact_name = output_path.name
            provider_name = result.get("provider", provider)
            messages.append(f"video task {index + 1} succeeded, output file: {artifact_name}")
            description = (
                f"The {index + 1}th video generated by video generation tool in "
                f"turn {current_turn}, step {current_step}, provider is {provider_name}, "
                f"mode is {mode}, prompt is {prompt}"
            )
            output_files.append(
                build_workspace_file_record(
                    output_path,
                    description=description,
                    source="expert",
                    name=artifact_name,
                    turn=current_turn,
                    step=current_step,
                    expert_step=current_expert_step,
                )
            )

        if not output_files:
            message = f"{self.name} all {len(result_list)} video generation tasks failed: {', '.join(messages)}"
            current_output = {"status": "error", "message": message}
            logger.error(message)
            yield self.format_event(message, {"current_output": current_output})
            return

        message = f"{self.name} has completed {len(result_list)} video generation tasks: {', '.join(messages)}"
        current_output = {"status": "success", "message": message, "output_files": output_files}
        logger.info(message)
        yield self.format_event(message, {"current_output": current_output})
