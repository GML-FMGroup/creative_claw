"""Artifact forwarding helpers for expert calls triggered from tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from google.adk.artifacts import BaseArtifactService
from google.adk.artifacts.base_artifact_service import ArtifactVersion
from typing_extensions import override

if TYPE_CHECKING:
    from google.adk.tools.tool_context import ToolContext


class ToolContextArtifactService(BaseArtifactService):
    """Forward child-agent artifact access through the parent tool context.

    This mirrors the behavior of ADK's internal forwarding artifact service
    used by `AgentTool`, while keeping the dependency local and explicit inside
    Creative Claw's runtime layer.
    """

    def __init__(self, tool_context: "ToolContext") -> None:
        self.tool_context = tool_context
        self._invocation_context = tool_context._invocation_context

    @override
    async def save_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        artifact,
        session_id: str | None = None,
        custom_metadata: dict[str, Any] | None = None,
    ) -> int:
        del app_name, user_id, session_id
        return await self.tool_context.save_artifact(
            filename=filename,
            artifact=artifact,
            custom_metadata=custom_metadata,
        )

    @override
    async def load_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None = None,
        version: int | None = None,
    ):
        del app_name, user_id, session_id
        return await self.tool_context.load_artifact(filename=filename, version=version)

    @override
    async def list_artifact_keys(
        self,
        *,
        app_name: str,
        user_id: str,
        session_id: str | None = None,
    ) -> list[str]:
        del app_name, user_id, session_id
        return await self.tool_context.list_artifacts()

    @override
    async def delete_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None = None,
    ) -> None:
        del app_name, user_id, session_id
        if self._invocation_context.artifact_service is None:
            raise ValueError("Artifact service is not initialized.")
        await self._invocation_context.artifact_service.delete_artifact(
            app_name=self._invocation_context.app_name,
            user_id=self._invocation_context.user_id,
            session_id=self._invocation_context.session.id,
            filename=filename,
        )

    @override
    async def list_versions(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None = None,
    ) -> list[int]:
        del app_name, user_id, session_id
        if self._invocation_context.artifact_service is None:
            raise ValueError("Artifact service is not initialized.")
        return await self._invocation_context.artifact_service.list_versions(
            app_name=self._invocation_context.app_name,
            user_id=self._invocation_context.user_id,
            session_id=self._invocation_context.session.id,
            filename=filename,
        )

    @override
    async def list_artifact_versions(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None = None,
    ) -> list[ArtifactVersion]:
        del app_name, user_id, filename, session_id
        raise NotImplementedError("list_artifact_versions is not implemented yet.")

    @override
    async def get_artifact_version(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: str | None = None,
        version: int | None = None,
    ) -> ArtifactVersion | None:
        del app_name, user_id, filename, session_id, version
        raise NotImplementedError("get_artifact_version is not implemented yet.")
