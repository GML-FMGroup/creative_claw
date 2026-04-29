"""Internal ADK-backed structured experts for short-video production."""

from __future__ import annotations

import json
import uuid
from typing import Any, TypeVar

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai.types import Content, Part
from pydantic import BaseModel, ConfigDict, Field

from conf.llm import build_llm, resolve_llm_model_name
from src.production.short_video.models import (
    ReferenceAssetEntry,
    ShortVideoStoryboard,
    ShortVideoStoryboardShot,
)
from src.production.short_video.prompt_catalog import render_prompt_template


SchemaT = TypeVar("SchemaT", bound=BaseModel)


class _StrictModel(BaseModel):
    """Base class for strict ADK structured-output schemas."""

    model_config = ConfigDict(extra="forbid")


class _AdkStoryboardShot(_StrictModel):
    """Strict storyboard shot returned by ShortVideoStoryboardExpert."""

    sequence_index: int = 1
    duration_seconds: float = 1.0
    purpose: str = ""
    visual_beat: str = ""
    dialogue_lines: list[str] = Field(default_factory=list)
    audio_notes: str = ""
    constraints: list[str] = Field(default_factory=list)
    reference_asset_ids: list[str] = Field(default_factory=list)

    def to_storyboard_shot(self) -> ShortVideoStoryboardShot:
        """Convert the strict ADK shot into the production storyboard model."""
        return ShortVideoStoryboardShot(
            sequence_index=self.sequence_index,
            duration_seconds=self.duration_seconds,
            purpose=self.purpose,
            visual_beat=self.visual_beat,
            dialogue_lines=self.dialogue_lines,
            audio_notes=self.audio_notes,
            constraints=self.constraints,
            reference_asset_ids=self.reference_asset_ids,
        )


class _AdkShortVideoStoryboard(_StrictModel):
    """Strict storyboard returned by ShortVideoStoryboardExpert."""

    title: str = ""
    narrative_summary: str = ""
    global_constraints: list[str] = Field(default_factory=list)
    shots: list[_AdkStoryboardShot] = Field(default_factory=list)
    notes: str = ""

    def to_storyboard(
        self,
        *,
        video_type: str,
        selected_ratio: str | None,
        duration_seconds: float,
        reference_asset_ids: list[str],
    ) -> ShortVideoStoryboard:
        """Convert strict ADK output into the production storyboard model."""
        return ShortVideoStoryboard(
            video_type=video_type,  # type: ignore[arg-type]
            title=self.title or "Short-video storyboard",
            narrative_summary=self.narrative_summary,
            target_duration_seconds=duration_seconds,
            selected_ratio=selected_ratio,  # type: ignore[arg-type]
            global_constraints=self.global_constraints,
            reference_asset_ids=reference_asset_ids,
            shots=[shot.to_storyboard_shot() for shot in self.shots],
        )


class ShortVideoStoryboardExpertRuntime:
    """Run internal short-video storyboard experts through ADK structured output."""

    def __init__(
        self,
        *,
        model_reference: str | None = None,
        app_name: str = "creative_claw_short_video_internal",
    ) -> None:
        """Initialize the internal short-video storyboard expert runtime."""
        self.model_reference = model_reference
        self.app_name = app_name

    async def plan_storyboard(
        self,
        *,
        user_prompt: str,
        video_type: str,
        selected_ratio: str | None,
        duration_seconds: float,
        reference_assets: list[ReferenceAssetEntry],
        baseline_storyboard: ShortVideoStoryboard,
    ) -> ShortVideoStoryboard:
        """Generate a reviewable storyboard from the user brief and baseline constraints."""
        reference_asset_ids = [asset.reference_asset_id for asset in reference_assets]
        prompt = render_prompt_template(
            "storyboard_expert",
            {
                "video_type": video_type,
                "selected_ratio": selected_ratio or "not selected",
                "duration_seconds": duration_seconds,
                "user_prompt": user_prompt,
                "reference_assets_json": _json_dump(
                    [asset.model_dump(mode="json") for asset in reference_assets]
                ),
                "baseline_storyboard_json": baseline_storyboard.model_dump_json(indent=2),
            },
        )
        adk_output = await self._run_structured_agent(
            agent_name="ShortVideoStoryboardExpert",
            instruction=(
                "You create concise, user-reviewable short-video storyboards. "
                "Return only fields requested by the schema."
            ),
            request_text=prompt,
            output_schema=_AdkShortVideoStoryboard,
            output_key="short_video_storyboard",
        )
        return adk_output.to_storyboard(
            video_type=video_type,
            selected_ratio=selected_ratio,
            duration_seconds=duration_seconds,
            reference_asset_ids=reference_asset_ids,
        )

    async def _run_structured_agent(
        self,
        *,
        agent_name: str,
        instruction: str,
        request_text: str,
        output_schema: type[SchemaT],
        output_key: str,
    ) -> SchemaT:
        """Run one ADK LlmAgent and parse its structured output."""
        agent = LlmAgent(
            name=agent_name,
            model=build_llm(self.model_reference),
            instruction=instruction,
            include_contents="none",
            output_schema=output_schema,
            output_key=output_key,
        )
        runner = InMemoryRunner(agent=agent, app_name=self.app_name)
        user_id = "short-video-production"
        session_id = f"{agent_name.lower()}_{uuid.uuid4().hex[:12]}"
        await runner.session_service.create_session(
            app_name=self.app_name,
            user_id=user_id,
            session_id=session_id,
            state={},
        )
        final_text = ""
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=Content(role="user", parts=[Part(text=request_text)]),
        ):
            if event.is_final_response() and event.content and event.content.parts:
                generated_text = next((part.text for part in event.content.parts if part.text), "")
                final_text = str(generated_text or "").strip()

        session = await runner.session_service.get_session(
            app_name=self.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        raw_output = session.state.get(output_key) if session is not None else None
        return _coerce_structured_output(raw_output or final_text, output_schema)

    @property
    def model_name(self) -> str:
        """Return the configured model name for observability."""
        return resolve_llm_model_name(self.model_reference)


def _coerce_structured_output(raw_output: Any, output_schema: type[SchemaT]) -> SchemaT:
    """Coerce ADK output_key or final text into one Pydantic model."""
    if isinstance(raw_output, output_schema):
        return raw_output
    if isinstance(raw_output, BaseModel):
        return output_schema.model_validate(raw_output.model_dump(mode="json"))
    if isinstance(raw_output, dict):
        return output_schema.model_validate(raw_output)
    if isinstance(raw_output, str):
        text = _strip_json_fence(raw_output)
        return output_schema.model_validate_json(text)
    return output_schema.model_validate(raw_output)


def _strip_json_fence(text: str) -> str:
    """Strip a simple Markdown JSON code fence when a provider returns one."""
    stripped = str(text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _json_dump(value: Any) -> str:
    """Dump prompt variables as stable JSON."""
    return json.dumps(value, ensure_ascii=False, indent=2)
