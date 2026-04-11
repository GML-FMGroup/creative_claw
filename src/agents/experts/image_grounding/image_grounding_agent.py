"""Image grounding expert agent."""

from __future__ import annotations

from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai.types import Content, Part
from typing_extensions import override

from src.agents.experts.image_grounding.tool import dino_xseek_detection_tool
from src.logger import logger


class ImageGroundingAgent(BaseAgent):
    """Ground a natural-language target description to bbox results in one image."""

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the image grounding expert agent."""
        super().__init__(name=name, description=description)

    def format_event(self, content_text: str = "", state_delta: dict[str, Any] | None = None) -> Event:
        """Build one ADK event with optional content text and state updates."""
        event = Event(author=self.name)
        if state_delta:
            event.actions = EventActions(state_delta=state_delta)
        if content_text:
            event.content = Content(role="model", parts=[Part(text=content_text)])
        return event

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run the DINO-XSeek grounding flow for one workspace image."""
        current_parameters = ctx.session.state.get("current_parameters", {})
        input_path = str(current_parameters.get("input_path", "")).strip()
        prompt = str(current_parameters.get("prompt", "")).strip()

        if not input_path or not prompt:
            error_text = f"Missing parameters provided to {self.name}, must include: input_path, prompt"
            current_output = {"status": "error", "message": error_text}
            logger.error(error_text)
            yield self.format_event(error_text, {"current_output": current_output})
            return

        model = str(current_parameters.get("model", "")).strip()
        result = await dino_xseek_detection_tool(
            ctx,
            input_path,
            prompt,
            **({"model": model} if model else {}),
        )
        status = str(result.get("status", "")).strip().lower()
        message = str(result.get("message", "")).strip()

        current_output = {
            "status": status or "error",
            "message": message,
            "message_for_user": message,
            "results": [
                {
                    "input_path": str(result.get("input_path", input_path)).strip() or input_path,
                    "prompt": str(result.get("prompt", prompt)).strip() or prompt,
                    "status": status or "error",
                    "message": message,
                    "objects": list(result.get("objects", []) or []),
                    "bboxes": list(result.get("bboxes", []) or []),
                    "task_uuid": str(result.get("task_uuid", "")).strip(),
                    "session_id": str(result.get("session_id", "")).strip(),
                    "provider": str(result.get("provider", "")).strip(),
                    "model_name": str(result.get("model_name", "")).strip(),
                }
            ],
        }
        yield self.format_event(
            message,
            {
                "current_output": current_output,
                "image_ground_results": current_output["results"],
            },
        )
        return
