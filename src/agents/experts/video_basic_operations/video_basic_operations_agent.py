"""Video basic operations expert agent."""

from __future__ import annotations

from typing import Any, AsyncGenerator
from typing_extensions import override

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai.types import Content, Part

from src.agents.experts.video_basic_operations.tool import run_video_basic_operation


class VideoBasicOperationsAgent(BaseAgent):
    """Run one deterministic video basic operation inside the workspace."""

    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the video basic operations expert."""
        super().__init__(name=name, description=description)

    def format_event(
        self,
        content_text: str | None = None,
        state_delta: dict[str, Any] | None = None,
    ) -> Event:
        """Build one ADK event with optional text content and state updates."""
        event = Event(author=self.name)
        if state_delta:
            event.actions = EventActions(state_delta=state_delta)
        if content_text:
            event.content = Content(role="model", parts=[Part(text=content_text)])
        return event

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run one normalized deterministic video operation request."""
        current_parameters = ctx.session.state.get("current_parameters", {})
        current_output = run_video_basic_operation(current_parameters)
        yield self.format_event(
            current_output.get("output_text") or current_output.get("message", ""),
            {
                "current_output": current_output,
                "video_basic_operation_results": current_output.get("results", {}),
            },
        )
