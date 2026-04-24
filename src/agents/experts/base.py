"""Shared base classes for Creative Claw expert agents."""

from __future__ import annotations

from typing import Any

from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.genai.types import Content, Part


class CreativeExpert(BaseAgent):
    """Base class for custom experts that emit ADK events directly."""

    model_config = {"arbitrary_types_allowed": True}

    def format_event(
        self,
        content_text: str | None = None,
        state_delta: dict[str, Any] | None = None,
    ) -> Event:
        """Build one ADK event with optional model text and session state updates."""
        event = Event(author=self.name)
        if state_delta:
            event.actions = EventActions(state_delta=state_delta)
        if content_text:
            event.content = Content(role="model", parts=[Part(text=content_text)])
        return event
