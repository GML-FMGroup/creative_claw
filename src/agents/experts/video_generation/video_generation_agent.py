"""Video generation expert for Creative Claw."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Dict, List
from typing_extensions import override

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai.types import Content, Part

from src.agents.experts.video_generation import tool as video_tools
from src.logger import logger
from src.runtime.workspace import build_workspace_file_record, save_binary_output


class VideoGenerationAgent(BaseAgent):
    """Generate one or more videos from prompt, image, or video-guided inputs."""

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the video generation expert."""
        super().__init__(name=name, sub_agents=[], description=description)

    def format_event(self, content_text: str | None = None, state_delta: Dict | None = None) -> Event:
        """Build one ADK event carrying text and/or state delta."""
        event = Event(author=self.name)
        if state_delta:
            event.actions = EventActions(state_delta=state_delta)
        if content_text:
            event.content = Content(role="model", parts=[Part(text=content_text)])
        return event

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

        provider = str(current_parameters.get("provider", "seedance")).strip().lower() or "seedance"
        mode = video_tools.normalize_video_mode(str(current_parameters.get("mode", "prompt")))
        if provider == "kling":
            aspect_ratio = video_tools.normalize_kling_aspect_ratio(
                current_parameters.get("aspect_ratio", "16:9")
            )
            resolution = ""
            duration_seconds = video_tools.normalize_kling_duration(
                current_parameters.get("duration_seconds", 5)
            )
        else:
            aspect_ratio = video_tools.normalize_video_aspect_ratio(
                current_parameters.get("aspect_ratio", "16:9")
            )
            resolution = video_tools.normalize_video_resolution(
                current_parameters.get("resolution", "720p")
            )
            duration_seconds = video_tools.normalize_video_duration(
                current_parameters.get("duration_seconds", 8)
            )
        negative_prompt = str(current_parameters.get("negative_prompt", "") or "").strip()
        kling_model_name = str(current_parameters.get("model_name", "") or "").strip()
        kling_mode = video_tools.normalize_kling_mode(current_parameters.get("kling_mode", "std"))

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
            enhance_prompt = video_tools.normalize_optional_boolean(
                current_parameters.get("enhance_prompt"),
                parameter_name="enhance_prompt",
            )
            person_generation = video_tools.normalize_person_generation(
                current_parameters.get("person_generation")
            )
            seed = video_tools.normalize_video_seed(current_parameters.get("seed"))
            video_tools._validate_mode_input_paths(mode, input_paths)
        except ValueError as exc:
            error_text = f"{self.name} got invalid parameters: {exc}"
            current_output = {"status": "error", "message": error_text}
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": current_output})
            return

        skip_local_prompt_enhancement = provider == "veo" and enhance_prompt is False
        if skip_local_prompt_enhancement:
            normalized_prompts = prompt_list
        else:
            enhanced_prompt_results = await asyncio.gather(
                *[
                    video_tools.prompt_enhancement_tool(ctx, prompt)
                    if prompt
                    else asyncio.sleep(0, result={"status": "success", "message": ""})
                    for prompt in prompt_list
                ]
            )
            normalized_prompts = []
            for original_prompt, result in zip(prompt_list, enhanced_prompt_results):
                if result["status"] == "success":
                    normalized_prompts.append(str(result["message"]).strip())
                else:
                    logger.warning("%s: prompt enhancement failed, using original prompt", self.name)
                    normalized_prompts.append(original_prompt)

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
                    enhance_prompt=enhance_prompt,
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
                )
                for prompt in normalized_prompts
            ]
        result_list = await asyncio.gather(*generation_tasks)

        output_files = []
        messages: list[str] = []
        for index, (prompt, result) in enumerate(zip(normalized_prompts, result_list)):
            if result["status"] == "error":
                messages.append(f"video task {index + 1} failed: {result['message']}")
                continue

            output_path = save_binary_output(
                result["message"],
                session_id=ctx.session.id,
                step=ctx.session.state.get("step", 0) + 1,
                output_type="video_generation",
                index=index,
                extension=".mp4",
            )
            artifact_name = output_path.name
            provider_name = result.get("provider", provider)
            messages.append(f"video task {index + 1} succeeded, output file: {artifact_name}")
            description = (
                f"The {index + 1}th video generated by video generation tool in "
                f"round {ctx.session.state.get('step', 0) + 1}, provider is {provider_name}, "
                f"mode is {mode}, prompt is {prompt}"
            )
            output_files.append(
                build_workspace_file_record(
                    output_path,
                    description=description,
                    source="expert",
                    name=artifact_name,
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
