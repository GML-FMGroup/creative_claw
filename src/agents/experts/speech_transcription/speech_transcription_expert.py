"""Speech transcription expert for Creative Claw."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator
from typing_extensions import override

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai.types import Content, Part

from src.agents.experts.speech_transcription.tool import parse_bool, speech_transcription_tool


class SpeechTranscriptionExpert(BaseAgent):
    """Transcribe one or more audio or video files into text."""

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the speech transcription expert."""
        super().__init__(name=name, description=description)

    def format_event(
        self,
        content_text: str | None = None,
        state_delta: dict[str, Any] | None = None,
    ) -> Event:
        """Build one ADK event with optional content and state updates."""
        event = Event(author=self.name)
        if state_delta:
            event.actions = EventActions(state_delta=state_delta)
        if content_text:
            event.content = Content(role="model", parts=[Part(text=content_text)])
        return event

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run one normalized transcription request."""
        current_parameters = ctx.session.state.get("current_parameters", {})
        input_paths = current_parameters.get("input_paths", current_parameters.get("input_path"))
        if isinstance(input_paths, str):
            input_paths = [input_paths]
        input_paths = [str(path).strip() for path in (input_paths or []) if str(path).strip()]
        language = str(current_parameters.get("language", "")).strip()
        timestamps = parse_bool(current_parameters.get("timestamps"))

        if not input_paths:
            error_text = f"Missing parameters provided to {self.name}, must include: input_path or input_paths"
            current_output = {"status": "error", "message": error_text}
            yield self.format_event(error_text, {"current_output": current_output})
            return

        result_list = await asyncio.gather(
            *[
                speech_transcription_tool(
                    ctx,
                    path,
                    language=language,
                    timestamps=timestamps,
                )
                for path in input_paths
            ],
            return_exceptions=True,
        )

        structured_results: list[dict[str, Any]] = []
        success_messages: list[str] = []
        error_messages: list[str] = []
        for path, result in zip(input_paths, result_list):
            if isinstance(result, Exception):
                structured_results.append(
                    {
                        "input_path": path,
                        "status": "error",
                        "message": f"{type(result).__name__}: {result}",
                    }
                )
                error_messages.append(f"media {path} transcription failed, reason: {type(result).__name__}: {result}\n")
                continue

            status = str(result.get("status", "")).strip().lower() or "error"
            message = str(result.get("message", "")).strip()
            structured_results.append(
                {
                    "input_path": str(result.get("input_path", path)).strip() or path,
                    "status": status,
                    "message": message,
                    "transcription_text": str(result.get("transcription_text", "")).strip(),
                    "basic_info": str(result.get("basic_info", "")).strip(),
                    "provider": str(result.get("provider", "")).strip(),
                    "model_name": str(result.get("model_name", "")).strip(),
                    "timestamps": bool(result.get("timestamps", timestamps)),
                }
            )
            if status == "success":
                success_messages.append(f"media {path}: transcription completed\n")
            else:
                error_messages.append(f"media {path} transcription failed, reason: {message}\n")

        if len(error_messages) == len(input_paths):
            error_text = f"All {len(input_paths)} transcription tasks failed:\n\n" + "\n".join(error_messages)
            current_output = {
                "status": "error",
                "message": error_text,
                "message_for_user": error_text,
                "results": structured_results,
            }
            yield self.format_event(
                error_text,
                {
                    "current_output": current_output,
                    "speech_transcription_results": structured_results,
                },
            )
            return

        message = f"Finished transcribing {len(input_paths)} media files with {len(success_messages)} successful results."
        output_text = message + "\n\n" + "\n".join(success_messages + error_messages)
        current_output = {
            "status": "success",
            "message": message,
            "message_for_user": message,
            "output_text": output_text,
            "results": structured_results,
        }
        yield self.format_event(
            output_text,
            {
                "current_output": current_output,
                "speech_transcription_results": structured_results,
            },
        )
