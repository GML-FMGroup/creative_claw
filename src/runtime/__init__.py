"""Runtime primitives for channel-driven Creative Claw execution."""

from __future__ import annotations

from importlib import import_module

__all__ = ["CreativeClawRuntime", "InboundMessage", "MessageAttachment", "WorkflowEvent"]


def __getattr__(name: str):
    """Lazily resolve runtime exports to avoid circular imports."""
    if name == "CreativeClawRuntime":
        return import_module(".workflow_service", __name__).CreativeClawRuntime
    if name in {"InboundMessage", "MessageAttachment", "WorkflowEvent"}:
        models = import_module(".models", __name__)
        return getattr(models, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
