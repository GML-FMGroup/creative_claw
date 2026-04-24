"""Video understanding expert for Creative Claw."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator
from typing_extensions import override

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from src.agents.experts.base import CreativeExpert
from src.agents.experts.video_understanding.tool import (
    _SUPPORTED_VIDEO_UNDERSTANDING_MODES,
    video_understanding_tool,
)


class VideoUnderstandingExpert(CreativeExpert):
    """Run one or more atomic video understanding requests."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the video understanding expert."""
        super().__init__(name=name, description=description)

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run one normalized video understanding request."""
        current_parameters = ctx.session.state.get("current_parameters", {})
        input_paths = current_parameters.get("input_paths", current_parameters.get("input_path"))
        if isinstance(input_paths, str):
            input_paths = [input_paths]
        input_paths = [str(path).strip() for path in (input_paths or []) if str(path).strip()]

        raw_modes = current_parameters.get("mode")
        if isinstance(raw_modes, str):
            modes = [raw_modes.strip().lower()] * len(input_paths)
        elif isinstance(raw_modes, list):
            modes = [str(mode).strip().lower() for mode in raw_modes if str(mode).strip()]
            if len(modes) == 1 and len(input_paths) > 1:
                modes = modes * len(input_paths)
        else:
            modes = []

        if not input_paths or not modes:
            error_text = f"Missing parameters provided to {self.name}, must include: input_path or input_paths, mode"
            current_output = {"status": "error", "message": error_text}
            yield self.format_event(error_text, {"current_output": current_output})
            return

        if len(modes) != len(input_paths):
            error_text = (
                f"Invalid parameters provided to {self.name}: `mode` must contain exactly one value "
                f"or match the number of input videos ({len(input_paths)})."
            )
            current_output = {"status": "error", "message": error_text}
            yield self.format_event(error_text, {"current_output": current_output})
            return

        invalid_modes = [mode for mode in modes if mode not in _SUPPORTED_VIDEO_UNDERSTANDING_MODES]
        if invalid_modes:
            error_text = (
                f"Invalid mode provided to {self.name}: {invalid_modes}. "
                f"Supported modes are: {sorted(_SUPPORTED_VIDEO_UNDERSTANDING_MODES)}."
            )
            current_output = {"status": "error", "message": error_text}
            yield self.format_event(error_text, {"current_output": current_output})
            return

        result_list = await asyncio.gather(
            *[video_understanding_tool(ctx, path, mode) for path, mode in zip(input_paths, modes)],
            return_exceptions=True,
        )

        success_messages: list[str] = []
        error_messages: list[str] = []
        structured_results: list[dict[str, Any]] = []
        for path, mode, result in zip(input_paths, modes, result_list):
            if isinstance(result, Exception):
                structured_results.append(
                    {
                        "input_path": path,
                        "mode": mode,
                        "status": "error",
                        "message": f"{type(result).__name__}: {result}",
                    }
                )
                error_messages.append(f"video {path} {mode} failed, reason: {type(result).__name__}: {result}\n")
                continue

            status = str(result.get("status", "")).strip().lower() or "error"
            message = str(result.get("message", "")).strip()
            structured_results.append(
                {
                    "input_path": str(result.get("input_path", path)).strip() or path,
                    "mode": str(result.get("mode", mode)).strip() or mode,
                    "status": status,
                    "message": message,
                    "analysis_text": str(result.get("analysis_text", "")).strip(),
                    "basic_info": str(result.get("basic_info", "")).strip(),
                    "provider": str(result.get("provider", "")).strip(),
                    "model_name": str(result.get("model_name", "")).strip(),
                }
            )
            if status == "success":
                success_messages.append(f"video {path} {mode}: {message}\n")
            else:
                error_messages.append(f"video {path} {mode} failed, reason: {message}\n")

        if len(error_messages) == len(input_paths):
            error_text = f"All {len(input_paths)} videos understanding failed:\n\n" + "\n".join(error_messages)
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
                    "video_understanding_results": structured_results,
                },
            )
            return

        message = f"Finished understanding {len(input_paths)} videos with {len(success_messages)} successful analyses."
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
                "video_understanding_results": structured_results,
            },
        )
