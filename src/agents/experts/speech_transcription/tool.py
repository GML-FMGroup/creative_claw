"""Tool helpers for speech transcription."""

from __future__ import annotations

import json
import mimetypes
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


def describe_media_metadata(input_path: str) -> str:
    """Return a compact metadata summary for one audio or video file."""
    toolbox = BuiltinToolbox()
    mime_type, _ = mimetypes.guess_type(input_path)
    raw_result = (
        toolbox.video_info(input_path)
        if mime_type and mime_type.startswith("video/")
        else toolbox.audio_info(input_path)
    )
    result_text = str(raw_result).strip()
    if not result_text or result_text.startswith("Error"):
        return "Basic media info unavailable."
    try:
        payload = json.loads(result_text)
    except json.JSONDecodeError:
        return f"Basic media info unavailable: {result_text}"

    if mime_type and mime_type.startswith("video/"):
        return (
            "Basic media info: "
            f"duration_seconds={payload.get('duration_seconds')}, "
            f"size={payload.get('width')}x{payload.get('height')}, "
            f"fps={payload.get('fps')}, "
            f"video_codec={payload.get('video_codec')}, "
            f"audio_codec={payload.get('audio_codec')}."
        )
    return (
        "Basic media info: "
        f"duration_seconds={payload.get('duration_seconds')}, "
        f"sample_rate={payload.get('sample_rate')}, "
        f"channels={payload.get('channels')}, "
        f"codec={payload.get('codec')}."
    )


def parse_bool(value: Any) -> bool:
    """Normalize one flexible boolean-like value."""
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _build_transcription_prompt(*, language: str, timestamps: bool) -> str:
    """Build one transcription request."""
    lines = [
        "Transcribe the spoken content in this audio or video file faithfully.",
        "Do not translate, summarize, or rewrite the content.",
        "Preserve the original language, wording, and ordering as much as possible.",
    ]
    if language.strip():
        lines.append(f"Expected primary language: {language.strip()}.")
    if timestamps:
        lines.append("Include concise timestamps in the format [MM:SS.mmm] at reasonable segment boundaries.")
    else:
        lines.append("Return plain transcript text without timestamps.")
    lines.append("If the speech is unintelligible or absent, say so briefly.")
    return "\n".join(lines)


async def speech_transcription_tool(
    ctx: InvocationContext,
    input_path: str,
    *,
    language: str = "",
    timestamps: bool = False,
) -> dict[str, Any]:
    """Transcribe one workspace audio or video file with the configured multimodal LLM."""
    resolved_path = None
    try:
        resolved_path = resolve_workspace_path(input_path)
        media_part = load_local_file_part(resolved_path)
        prompt_text = _build_transcription_prompt(language=language, timestamps=timestamps)

        def before_model_callback(
            callback_context: CallbackContext,
            llm_request: LlmRequest,
        ) -> None:
            """Inject the media file and transcription request."""
            llm_request.contents.append(Content(role="user", parts=[Part(text=prompt_text), media_part]))

        llm = LlmAgent(
            name="SpeechTranscriptionToolAgent",
            model=build_llm(),
            instruction="You are a precise speech transcriber. Return only the faithful transcript.",
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
                "message": "Speech transcription returned empty text.",
                "input_path": workspace_relative_path(resolved_path),
                "provider": "google_adk",
                "model_name": resolve_llm_model_name(),
            }

        basic_info = describe_media_metadata(workspace_relative_path(resolved_path))
        return {
            "status": "success",
            "message": f"{output_text}\n\n{basic_info}",
            "transcription_text": output_text,
            "basic_info": basic_info,
            "input_path": workspace_relative_path(resolved_path),
            "provider": "google_adk",
            "model_name": resolve_llm_model_name(),
            "timestamps": timestamps,
        }
    except Exception as exc:
        logger.opt(exception=exc).error(
            "speech transcription failed: input_path={} resolved_path={} error_type={} error={!r}",
            input_path,
            resolved_path or "<unresolved>",
            type(exc).__name__,
            exc,
        )
        return {
            "status": "error",
            "message": (
                f"Speech transcription failed for '{input_path}' "
                f"(resolved='{resolved_path or '<unresolved>'}'): {type(exc).__name__}: {exc}"
            ),
            "input_path": str(input_path),
            "provider": "google_adk",
            "model_name": resolve_llm_model_name(),
            "timestamps": timestamps,
        }
