"""Image basic operations expert agent."""

from __future__ import annotations

from typing import AsyncGenerator
from typing_extensions import override

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from src.agents.experts.base import CreativeExpert
from src.agents.experts.image_basic_operations.tool import run_image_basic_operation


class ImageBasicOperationsAgent(CreativeExpert):
    """Run one deterministic image basic operation inside the workspace."""

    def __init__(self, name: str, description: str = "") -> None:
        """Initialize the image basic operations expert."""
        super().__init__(name=name, description=description)

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run one normalized deterministic image operation request."""
        current_parameters = dict(ctx.session.state.get("current_parameters", {}))
        current_parameters["__session_id"] = ctx.session.id
        current_parameters["__turn_index"] = int(ctx.session.state.get("turn_index", 0) or 0)
        current_parameters["__step"] = int(ctx.session.state.get("step", 0) or 0)
        current_parameters["__expert_step"] = int(ctx.session.state.get("expert_step", 0) or 0)
        current_output = run_image_basic_operation(current_parameters)
        yield self.format_event(
            current_output.get("output_text") or current_output.get("message", ""),
            {
                "current_output": current_output,
                "image_basic_operation_results": current_output.get("results", {}),
            },
        )
