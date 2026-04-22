"""Speech recognition expert for Creative Claw."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator
from typing_extensions import override

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai.types import Content, Part

from src.agents.experts.speech_recognition.tool import (
    infer_subtitle_output_path,
    normalize_subtitle_format,
    parse_bool,
    resolve_speech_task,
    resolve_subtitle_format,
    speech_recognition_tool,
    speech_subtitle_tool,
)
from src.runtime.workspace import (
    build_generated_output_path,
    build_workspace_file_record,
    resolve_workspace_path,
)


class SpeechRecognitionExpert(BaseAgent):
    """Recognize speech from media files or generate subtitle files."""

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the speech recognition expert."""
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
        """Run one normalized speech recognition request."""
        current_parameters = ctx.session.state.get("current_parameters", {})
        input_paths = current_parameters.get("input_paths", current_parameters.get("input_path"))
        if isinstance(input_paths, str):
            input_paths = [input_paths]
        input_paths = [str(path).strip() for path in (input_paths or []) if str(path).strip()]
        language = str(current_parameters.get("language", "")).strip()
        requested_timestamps = parse_bool(current_parameters.get("timestamps"))
        effective_task = resolve_speech_task(current_parameters)
        effective_timestamps = requested_timestamps or effective_task == "subtitle"
        subtitle_format = resolve_subtitle_format(current_parameters)
        output_path = str(current_parameters.get("output_path", "")).strip()
        subtitle_text = str(current_parameters.get("subtitle_text", current_parameters.get("audio_text", ""))).strip()
        caption_type = str(current_parameters.get("caption_type", current_parameters.get("audio_type", "auto"))).strip()
        sta_punc_mode = str(current_parameters.get("sta_punc_mode", "")).strip()
        words_per_line = _coerce_optional_int(current_parameters.get("words_per_line"))
        max_lines = _coerce_optional_int(current_parameters.get("max_lines"))
        use_itn = parse_bool(current_parameters.get("use_itn", current_parameters.get("enable_itn", True)))
        use_punc = parse_bool(current_parameters.get("use_punc", current_parameters.get("enable_punc", True)))
        use_ddc = parse_bool(current_parameters.get("use_ddc", current_parameters.get("enable_ddc", False)))
        use_speaker_info = parse_bool(
            current_parameters.get(
                "with_speaker_info",
                current_parameters.get("enable_speaker_info", False),
            )
        )
        use_capitalize = parse_bool(current_parameters.get("use_capitalize", True))

        if not input_paths:
            error_text = f"Missing parameters provided to {self.name}, must include: input_path or input_paths"
            current_output = {"status": "error", "message": error_text}
            yield self.format_event(error_text, {"current_output": current_output})
            return

        if effective_task == "subtitle" and output_path and len(input_paths) != 1:
            error_text = "Subtitle generation only supports output_path when exactly one input file is provided."
            current_output = {"status": "error", "message": error_text}
            yield self.format_event(error_text, {"current_output": current_output})
            return

        result_list = await asyncio.gather(
            *[
                (
                    speech_subtitle_tool(
                        ctx,
                        path,
                        language=language,
                        subtitle_format=subtitle_format,
                        caption_type=caption_type,
                        subtitle_text=subtitle_text,
                        sta_punc_mode=sta_punc_mode,
                        words_per_line=words_per_line,
                        max_lines=max_lines,
                        use_itn=use_itn,
                        use_punc=use_punc,
                        use_ddc=use_ddc,
                        with_speaker_info=use_speaker_info,
                        use_capitalize=use_capitalize,
                    )
                    if effective_task == "subtitle"
                    else speech_recognition_tool(
                        ctx,
                        path,
                        language=language,
                        timestamps=effective_timestamps,
                        task=effective_task,
                        enable_itn=use_itn,
                        enable_punc=use_punc,
                        enable_ddc=use_ddc,
                        enable_speaker_info=use_speaker_info,
                    )
                )
                for path in input_paths
            ],
            return_exceptions=True,
        )

        current_turn = int(ctx.session.state.get("turn_index", 0) or 0)
        current_step = int(ctx.session.state.get("step", 0) or 0)
        current_expert_step = int(ctx.session.state.get("expert_step", 0) or 0)
        structured_results: list[dict[str, Any]] = []
        output_files: list[dict[str, str]] = []
        success_messages: list[str] = []
        error_messages: list[str] = []

        for index, (path, result) in enumerate(zip(input_paths, result_list)):
            if isinstance(result, Exception):
                structured_results.append(
                    {
                        "input_path": path,
                        "status": "error",
                        "message": f"{type(result).__name__}: {result}",
                        "task": effective_task,
                        "timestamps": effective_timestamps,
                    }
                )
                error_messages.append(f"media {path} speech recognition failed, reason: {type(result).__name__}: {result}\n")
                continue

            status = str(result.get("status", "")).strip().lower() or "error"
            message = str(result.get("message", "")).strip()
            current_result = {
                "input_path": str(result.get("input_path", path)).strip() or path,
                "status": status,
                "message": message,
                "task": str(result.get("task", effective_task)).strip() or effective_task,
                "transcription_text": str(result.get("transcription_text", "")).strip(),
                "basic_info": str(result.get("basic_info", "")).strip(),
                "provider": str(result.get("provider", "")).strip(),
                "model_name": str(result.get("model_name", "")).strip(),
                "timestamps": bool(result.get("timestamps", effective_timestamps)),
                "utterances": result.get("utterances", []) if isinstance(result.get("utterances"), list) else [],
                "audio_duration_ms": result.get("audio_duration_ms"),
                "request_id": str(result.get("request_id", "")).strip(),
                "log_id": str(result.get("log_id", "")).strip(),
                "job_id": str(result.get("job_id", "")).strip(),
                "subtitle_backend": str(result.get("subtitle_backend", "")).strip(),
                "caption_type": str(result.get("caption_type", "")).strip(),
                "subtitle_path": "",
                "subtitle_format": "",
            }

            if status == "success" and effective_task == "subtitle":
                try:
                    subtitle_path = self._write_subtitle_output(
                        ctx,
                        input_path=current_result["input_path"],
                        subtitle_text=str(result.get("subtitle_content", "")).strip(),
                        subtitle_format=subtitle_format,
                        index=index,
                        explicit_output_path=output_path,
                    )
                    output_record = build_workspace_file_record(
                        subtitle_path,
                        description=(
                            f"Subtitle generated by {self.name} using format={normalize_subtitle_format(subtitle_format)} "
                            f"from media={current_result['input_path']}."
                        ),
                        source="expert",
                        name=Path(subtitle_path).name,
                        turn=current_turn,
                        step=current_step,
                        expert_step=current_expert_step,
                    )
                    current_result["subtitle_path"] = output_record["path"]
                    current_result["subtitle_format"] = normalize_subtitle_format(subtitle_format)
                    output_files.append(output_record)
                    success_messages.append(
                        f"media {path}: subtitle generated -> {current_result['subtitle_path']}\n"
                    )
                except Exception as exc:
                    current_result["status"] = "error"
                    current_result["message"] = f"Subtitle generation failed: {type(exc).__name__}: {exc}"
                    error_messages.append(
                        f"media {path} subtitle generation failed, reason: {type(exc).__name__}: {exc}\n"
                    )
            elif status == "success":
                success_messages.append(f"media {path}: speech recognition completed\n")
            else:
                error_messages.append(f"media {path} speech recognition failed, reason: {message}\n")

            structured_results.append(current_result)

        success_count = sum(1 for item in structured_results if item["status"] == "success")
        if success_count == 0:
            error_text = f"All {len(input_paths)} speech recognition tasks failed:\n\n" + "\n".join(error_messages)
            current_output = {
                "status": "error",
                "message": error_text,
                "message_for_user": error_text,
                "results": structured_results,
                "output_files": output_files,
            }
            yield self.format_event(
                error_text,
                {
                    "current_output": current_output,
                    "speech_recognition_results": structured_results,
                    "speech_transcription_results": structured_results,
                },
            )
            return

        message = (
            f"Finished {effective_task} processing for {len(input_paths)} media files "
            f"with {success_count} successful results."
        )
        output_text = message + "\n\n" + "\n".join(success_messages + error_messages)
        current_output = {
            "status": "success",
            "message": message,
            "message_for_user": message,
            "output_text": output_text,
            "output_files": output_files,
            "results": structured_results,
        }
        yield self.format_event(
            output_text,
            {
                "current_output": current_output,
                "speech_recognition_results": structured_results,
                "speech_transcription_results": structured_results,
            },
        )

    def _write_subtitle_output(
        self,
        ctx: InvocationContext,
        *,
        input_path: str,
        subtitle_text: str,
        subtitle_format: str,
        index: int,
        explicit_output_path: str = "",
    ) -> Path:
        """Persist one generated subtitle document inside the workspace."""
        if explicit_output_path:
            destination = resolve_workspace_path(explicit_output_path)
        else:
            inferred_name = infer_subtitle_output_path(input_path, subtitle_format)
            destination = build_generated_output_path(
                session_id=ctx.session.id,
                turn_index=int(ctx.session.state.get("turn_index", 0) or 0),
                step=int(ctx.session.state.get("step", 0) or 0),
                output_type=f"speech_recognition_{Path(inferred_name).stem}",
                index=index,
                extension=f".{normalize_subtitle_format(subtitle_format)}",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(subtitle_text, encoding="utf-8")
        return destination.resolve()


def _coerce_optional_int(value: Any) -> int | None:
    """Normalize one optional integer-like input."""
    if value in ("", None):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
