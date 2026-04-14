"""Text transform expert for Creative Claw."""

from __future__ import annotations

from typing import Any, AsyncGenerator
from typing_extensions import override

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai.types import Content, Part

from src.agents.experts.text_transform.tool import (
    _SUPPORTED_TEXT_TRANSFORM_MODES,
    normalize_text_transform_mode,
    transform_text_tool,
)


class TextTransformExpert(BaseAgent):
    """Run one atomic text transformation."""

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the text transform expert."""
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
        """Run one atomic text transform request."""
        current_parameters = ctx.session.state.get("current_parameters", {})
        input_text = str(current_parameters.get("input_text", current_parameters.get("text", ""))).strip()
        raw_mode = str(current_parameters.get("mode", "")).strip().lower()
        target_language = str(current_parameters.get("target_language", "")).strip()
        style = str(current_parameters.get("style", "")).strip()
        constraints = str(current_parameters.get("constraints", "")).strip()

        if not input_text or not raw_mode:
            error_text = f"Missing parameters provided to {self.name}, must include: input_text or text, mode"
            current_output = {"status": "error", "message": error_text}
            yield self.format_event(error_text, {"current_output": current_output})
            return

        if raw_mode not in _SUPPORTED_TEXT_TRANSFORM_MODES:
            error_text = (
                f"Invalid mode provided to {self.name}: {raw_mode}. "
                f"Supported modes are: {sorted(_SUPPORTED_TEXT_TRANSFORM_MODES)}."
            )
            current_output = {"status": "error", "message": error_text}
            yield self.format_event(error_text, {"current_output": current_output})
            return

        result = await transform_text_tool(
            ctx,
            input_text=input_text,
            mode=normalize_text_transform_mode(raw_mode),
            target_language=target_language,
            style=style,
            constraints=constraints,
        )
        if result["status"] == "error":
            current_output = {"status": "error", "message": result["message"]}
            yield self.format_event(result["message"], {"current_output": current_output})
            return

        transformed_text = str(result["message"]).strip()
        current_output = {
            "status": "success",
            "message": f"{self.name} completed mode={raw_mode}.",
            "message_for_user": transformed_text,
            "output_text": transformed_text,
            "mode": raw_mode,
            "transformed_text": transformed_text,
            "provider": result.get("provider", ""),
            "model_name": result.get("model_name", ""),
        }
        yield self.format_event(
            transformed_text,
            {
                "current_output": current_output,
                "text_transform_results": current_output,
            },
        )
