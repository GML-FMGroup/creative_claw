"""Structured final response contract for the Creative Claw orchestrator."""

from __future__ import annotations

from pydantic import BaseModel, Field


ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY = "orchestrator_final_response"


class OrchestratorFinalResponse(BaseModel):
    """One final user-visible reply plus optional workspace attachments."""

    reply_text: str = Field(
        min_length=1,
        description="The full natural-language reply that should be shown to the user.",
    )
    final_file_paths: list[str] = Field(
        default_factory=list,
        description="Workspace-relative file paths that should be attached to the final reply.",
    )
