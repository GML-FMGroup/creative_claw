"""Speech synthesis expert for Creative Claw."""

from __future__ import annotations

from typing import AsyncGenerator
from typing_extensions import override

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from src.agents.experts.base import CreativeExpert
from src.agents.experts.speech_synthesis.tool import (
    _parse_bool,
    normalize_speech_audio_format,
    speech_synthesis_tool,
)
from src.runtime.workspace import build_workspace_file_record, save_binary_output


class SpeechSynthesisExpert(CreativeExpert):
    """Generate one speech audio file from text or SSML."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the speech synthesis expert."""
        super().__init__(name=name, description=description)

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run one speech synthesis request."""
        current_parameters = ctx.session.state.get("current_parameters", {})
        text = str(current_parameters.get("text", "")).strip()
        ssml = str(current_parameters.get("ssml", "")).strip()
        speaker = str(current_parameters.get("speaker", "")).strip()
        voice_type = str(current_parameters.get("voice_type", "")).strip()
        voice_name = str(current_parameters.get("voice_name", "")).strip()
        resource_id = str(current_parameters.get("resource_id", "")).strip()
        audio_format = normalize_speech_audio_format(current_parameters.get("audio_format", "mp3"))
        sample_rate = int(current_parameters.get("sample_rate", 24000) or 24000)
        explicit_language = str(current_parameters.get("language", current_parameters.get("explicit_language", ""))).strip()
        enable_timestamp = _parse_bool(current_parameters.get("enable_timestamp", False))
        latex_parser = str(current_parameters.get("latex_parser", "")).strip()

        if not text and not ssml:
            error_text = f"Missing parameters provided to {self.name}, must include: text or ssml"
            current_output = {"status": "error", "message": error_text}
            yield self.format_event(error_text, {"current_output": current_output})
            return

        result = await speech_synthesis_tool(
            user_id=str(ctx.session.user_id),
            text=text,
            ssml=ssml,
            speaker=speaker,
            voice_type=voice_type,
            voice_name=voice_name,
            resource_id=resource_id,
            audio_format=audio_format,
            sample_rate=sample_rate,
            explicit_language=explicit_language,
            enable_timestamp=enable_timestamp,
            latex_parser=latex_parser,
        )
        if result["status"] != "success":
            current_output = {"status": "error", "message": result["message"]}
            yield self.format_event(result["message"], {"current_output": current_output})
            return

        current_turn = int(ctx.session.state.get("turn_index", 0) or 0)
        current_step = int(ctx.session.state.get("step", 0) or 0)
        current_expert_step = int(ctx.session.state.get("expert_step", 0) or 0)
        output_path = save_binary_output(
            result["message"],
            session_id=ctx.session.id,
            turn_index=current_turn,
            step=current_step,
            output_type="speech_synthesis",
            index=0,
            extension=f".{audio_format}",
        )
        output_record = build_workspace_file_record(
            output_path,
            description=(
                f"Speech synthesized by ByteDance TTS with speaker={result.get('speaker', '')}, "
                f"resource_id={result.get('model_name', '')}, format={audio_format}."
            ),
            source="expert",
            name=output_path.name,
            turn=current_turn,
            step=current_step,
            expert_step=current_expert_step,
        )
        synthesis_result = {
            "output_path": output_record["path"],
            "speaker": str(result.get("speaker", "")).strip(),
            "voice_name": str(result.get("voice_name", "")).strip(),
            "resource_id": str(result.get("model_name", "")).strip(),
            "audio_format": audio_format,
            "usage": result.get("usage", {}),
            "log_id": str(result.get("log_id", "")).strip(),
            "sentence_count": len(result.get("sentences", []) or []),
            "provider": str(result.get("provider", "")).strip(),
        }
        message = f"{self.name} generated 1 speech file: {output_path.name}"
        current_output = {
            "status": "success",
            "message": message,
            "message_for_user": message,
            "output_text": message,
            "output_files": [output_record],
            "results": [synthesis_result],
        }
        yield self.format_event(
            message,
            {
                "current_output": current_output,
                "speech_synthesis_results": [synthesis_result],
            },
        )
