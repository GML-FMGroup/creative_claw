"""Shared typed models for production workflows."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


ProductionStatus = Literal[
    "running",
    "needs_user_review",
    "needs_user_input",
    "completed",
    "failed",
    "cancelled",
]


def utc_now_iso() -> str:
    """Return a stable UTC timestamp string for persisted production records."""
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    """Return a compact unique identifier with a readable prefix."""
    cleaned_prefix = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in prefix)
    return f"{cleaned_prefix}_{uuid.uuid4().hex[:12]}"


class WorkspaceFileRef(BaseModel):
    """Workspace-relative file reference returned to users or persisted in state."""

    name: str
    path: str
    description: str = ""
    source: str = ""


class ProductionOwnerRef(BaseModel):
    """Conversation owner used to protect production session reads."""

    channel: str = ""
    chat_id: str = ""
    sender_id: str = ""


class ProductionSession(BaseModel):
    """Metadata for one durable production session."""

    production_session_id: str
    capability: str
    adk_session_id: str
    turn_index: int
    owner_ref: ProductionOwnerRef = Field(default_factory=ProductionOwnerRef)
    root_dir: str
    status: ProductionStatus
    created_at: str
    updated_at: str


class ProductionEvent(BaseModel):
    """Append-only event used for progress, audit, and debugging."""

    event_id: str = Field(default_factory=lambda: new_id("event"))
    event_type: str
    stage: str
    message: str
    created_at: str = Field(default_factory=utc_now_iso)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProductionErrorInfo(BaseModel):
    """Serializable failure details returned by production tools."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ReviewPayload(BaseModel):
    """Structured user review payload for production breakpoints."""

    review_type: str
    title: str
    summary: str = ""
    items: list[dict[str, Any]] = Field(default_factory=list)
    options: list[dict[str, Any]] = Field(default_factory=list)


class ProductionBreakpoint(BaseModel):
    """Active pause point waiting for user review or input."""

    breakpoint_id: str = Field(default_factory=lambda: new_id("breakpoint"))
    stage: str
    review_payload: ReviewPayload
    created_at: str = Field(default_factory=utc_now_iso)


class ProductionState(BaseModel):
    """Base persisted state shared by all production capabilities."""

    state_schema_version: str = "0.1.0"
    production_session: ProductionSession
    status: ProductionStatus
    stage: str
    progress_percent: int = 0
    active_breakpoint: ProductionBreakpoint | None = None
    production_events: list[ProductionEvent] = Field(default_factory=list)
    artifacts: list[WorkspaceFileRef] = Field(default_factory=list)


class ProductionSessionIndexEntry(BaseModel):
    """Compact index entry for discovering and loading production sessions."""

    production_session_id: str
    capability: str
    adk_session_id: str
    owner_ref: ProductionOwnerRef = Field(default_factory=ProductionOwnerRef)
    state_ref: str
    status: ProductionStatus
    stage: str
    artifacts: list[WorkspaceFileRef] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ProductionRunResult(BaseModel):
    """Uniform result returned by one production tool invocation."""

    status: ProductionStatus
    capability: str
    production_session_id: str
    stage: str
    progress_percent: int
    message: str
    state_ref: str | None = None
    artifacts: list[WorkspaceFileRef] = Field(default_factory=list)
    review_payload: ReviewPayload | None = None
    error: ProductionErrorInfo | None = None
    events: list[ProductionEvent] = Field(default_factory=list)

