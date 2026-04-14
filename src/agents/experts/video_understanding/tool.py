"""Tool helpers for video understanding."""

from __future__ import annotations

import json
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.invocation_context import InvocationContext
from google.adk.models import LlmRequest
from google.genai.types import Content, Part

from conf.llm import build_llm, resolve_llm_model_name
from src.logger import logger
from src.runtime.workspace import load_local_file_part, resolve_workspace_path, workspace_relative_path
from src.tools.builtin_tools import BuiltinToolbox

_SUPPORTED_VIDEO_UNDERSTANDING_MODES = {"description", "shot_breakdown", "ocr", "prompt"}


def _describe_video_metadata(input_path: str) -> str:
    """Return a compact metadata summary for one video."""
    toolbox = BuiltinToolbox()
    raw_result = str(toolbox.video_info(input_path)).strip()
    if not raw_result or raw_result.startswith("Error"):
        return "Basic video info unavailable."
    try:
        payload = json.loads(raw_result)
    except json.JSONDecodeError:
        return f"Basic video info unavailable: {raw_result}"

    return (
        "Basic video info: "
        f"duration_seconds={payload.get('duration_seconds')}, "
        f"size={payload.get('width')}x{payload.get('height')}, "
        f"fps={payload.get('fps')}, "
        f"video_codec={payload.get('video_codec')}, "
        f"audio_codec={payload.get('audio_codec')}."
    )


def _build_video_prompt(mode: str) -> str:
    """Return the analysis prompt for one video understanding mode."""
    prompts_map = {
        "description": (
            "Describe the video clearly. Summarize the scene, subjects, actions, setting, mood, and major visual changes over time."
        ),
        "shot_breakdown": (
            "Break the video into a concise shot-by-shot or beat-by-beat breakdown. "
            "For each shot, describe the main subject, camera or view change, visible action, and approximate order."
        ),
        "ocr": (
            "Extract all readable text visible in the video. Preserve the original language and keep separate text segments clearly organized."
        ),
        "prompt": (
            "Reverse engineer a reusable creative prompt from the video. Infer the likely style, subject, motion, scene, lighting, atmosphere, "
            "camera language, composition, and any visible text or graphics that matter for recreation."
        ),
    }
    return prompts_map.get(mode, prompts_map["description"])


async def video_understanding_tool(
    ctx: InvocationContext,
    input_path: str,
    mode: str = "description",
) -> dict[str, Any]:
    """Analyze one workspace video with the configured multimodal LLM."""
    normalized_mode = str(mode or "description").strip().lower()
    resolved_path = None
    try:
        resolved_path = resolve_workspace_path(input_path)
        video_part = load_local_file_part(resolved_path)
        prompt_text = _build_video_prompt(normalized_mode)

        def before_model_callback(
            callback_context: CallbackContext,
            llm_request: LlmRequest,
        ) -> None:
            """Inject the video file and mode-specific prompt."""
            llm_request.contents.append(
                Content(role="user", parts=[Part(text=prompt_text), video_part])
            )

        llm = LlmAgent(
            name="VideoUnderstandingToolAgent",
            model=build_llm(),
            instruction="You are a professional video analyst. Follow the requested mode exactly.",
            include_contents="none",
            before_model_callback=before_model_callback,
        )

        output_text = ""
        async for event in llm.run_async(ctx):
            if event.is_final_response() and event.content and event.content.parts:
                generated_text = next((part.text for part in event.content.parts if part.text), None)
                if generated_text:
                    output_text = generated_text.strip()

        if not output_text:
            return {
                "status": "error",
                "message": "Video understanding returned empty text.",
                "input_path": workspace_relative_path(resolved_path),
                "mode": normalized_mode,
                "provider": "google_adk",
                "model_name": resolve_llm_model_name(),
            }

        basic_info = _describe_video_metadata(workspace_relative_path(resolved_path))
        return {
            "status": "success",
            "message": f"{output_text}\n\n{basic_info}",
            "analysis_text": output_text,
            "basic_info": basic_info,
            "input_path": workspace_relative_path(resolved_path),
            "mode": normalized_mode,
            "provider": "google_adk",
            "model_name": resolve_llm_model_name(),
        }
    except Exception as exc:
        logger.opt(exception=exc).error(
            "video understanding failed: input_path={} resolved_path={} mode={} error_type={} error={!r}",
            input_path,
            resolved_path or "<unresolved>",
            normalized_mode,
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": (
                f"Video understanding failed for '{input_path}' "
                f"(resolved='{resolved_path or '<unresolved>'}', mode='{normalized_mode}'): "
                f"{type(exc).__name__}: {exc}"
            ),
            "input_path": str(input_path),
            "mode": normalized_mode,
            "provider": "google_adk",
            "model_name": resolve_llm_model_name(),
        }
