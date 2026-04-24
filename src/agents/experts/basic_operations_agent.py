"""Shared agent wrapper for deterministic basic-operation experts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, AsyncGenerator

from pydantic import PrivateAttr
from typing_extensions import override

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from src.agents.experts.base import CreativeExpert

BasicOperationRunner = Callable[[dict[str, Any]], dict[str, Any]]


class BasicOperationsAgent(CreativeExpert):
    """Run one deterministic basic operation through a media-specific runner."""

    _operation_runner: BasicOperationRunner = PrivateAttr()
    _results_key: str = PrivateAttr()

    def __init__(
        self,
        name: str,
        *,
        operation_runner: BasicOperationRunner,
        results_key: str,
        description: str = "",
    ) -> None:
        """Initialize a deterministic basic-operation expert wrapper."""
        super().__init__(name=name, description=description)
        self._operation_runner = operation_runner
        self._results_key = results_key

    @override
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """Run one normalized deterministic media operation request."""
        current_parameters = dict(ctx.session.state.get("current_parameters", {}))
        current_parameters["__session_id"] = ctx.session.id
        current_parameters["__turn_index"] = int(ctx.session.state.get("turn_index", 0) or 0)
        current_parameters["__step"] = int(ctx.session.state.get("step", 0) or 0)
        current_parameters["__expert_step"] = int(ctx.session.state.get("expert_step", 0) or 0)
        current_output = self._operation_runner(current_parameters)
        yield self.format_event(
            current_output.get("output_text") or current_output.get("message", ""),
            {
                "current_output": current_output,
                self._results_key: current_output.get("results", {}),
            },
        )

