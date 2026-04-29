"""Internal ADK-backed structured experts for PPT production."""

from __future__ import annotations

import json
import uuid
from typing import Any, TypeVar

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai.types import Content, Part
from pydantic import BaseModel, ConfigDict, Field

from conf.llm import build_llm, resolve_llm_model_name
from src.production.ppt.models import (
    DeckSlide,
    DeckSpec,
    DocumentSummary,
    IngestEntry,
    PPTRenderSettings,
    PPTLayoutType,
    PPTOutline,
    PPTOutlineEntry,
    TemplateSummary,
)
from src.production.ppt.prompt_catalog import render_prompt_template

SchemaT = TypeVar("SchemaT", bound=BaseModel)

_ALLOWED_LAYOUTS: set[str] = {"cover", "section", "content", "metric", "two_column", "closing"}


class PPTExpertRuntimeError(RuntimeError):
    """Raised when an internal PPT expert returns unusable structured output."""


class _StrictModel(BaseModel):
    """Base class for strict ADK structured-output schemas."""

    model_config = ConfigDict(extra="forbid")


class _AdkPPTOutlineEntry(_StrictModel):
    """One slide-level outline entry returned by the PPT outline expert."""

    sequence_index: int = 0
    title: str = ""
    purpose: str = ""
    layout_type: str = "content"
    bullet_points: list[str] = Field(default_factory=list)
    speaker_notes: str = ""
    source_refs: list[str] = Field(default_factory=list)


class _AdkPPTOutlinePlan(_StrictModel):
    """Strict outline plan returned by the PPT outline expert."""

    title: str = ""
    entries: list[_AdkPPTOutlineEntry] = Field(default_factory=list)
    notes: str = ""


class _AdkDeckSlide(_StrictModel):
    """One executable deck slide returned by the PPT deck-spec expert."""

    sequence_index: int = 0
    title: str = ""
    layout_type: str = "content"
    bullets: list[str] = Field(default_factory=list)
    visual_notes: str = ""
    speaker_notes: str = ""
    source_refs: list[str] = Field(default_factory=list)


class _AdkDeckSpec(_StrictModel):
    """Strict deck spec returned by the PPT deck-spec expert."""

    title: str = ""
    slides: list[_AdkDeckSlide] = Field(default_factory=list)
    notes: str = ""


class PPTExpertRuntime:
    """Run internal structured ADK experts for PPT outline and deck-spec planning."""

    def __init__(
        self,
        *,
        model_reference: str | None = None,
        app_name: str = "creative_claw_ppt_internal",
    ) -> None:
        """Initialize the internal PPT expert runtime."""
        self.model_reference = model_reference
        self.app_name = app_name

    async def plan_outline(
        self,
        *,
        brief: str,
        settings: PPTRenderSettings,
        inputs: list[IngestEntry],
        document_summary: DocumentSummary | None = None,
        template_summary: TemplateSummary | None = None,
    ) -> PPTOutline:
        """Generate a reviewable PPT outline from brief and input context."""
        prompt = render_prompt_template(
            "outline_instruction",
            {
                "brief": brief,
                "target_pages": settings.target_pages,
                "style_preset": settings.style_preset,
                "input_context": _input_context_json(inputs, document_summary, template_summary),
            },
        )
        plan = await self._run_structured_agent(
            agent_name="PPTOutlineExpert",
            instruction=(
                "You create concise, reviewable presentation outlines. "
                "Return only fields requested by the schema."
            ),
            request_text=prompt,
            output_schema=_AdkPPTOutlinePlan,
            output_key="ppt_outline_plan",
        )
        return _to_ppt_outline(plan, target_pages=settings.target_pages, valid_source_refs=_valid_source_refs(inputs))

    async def build_deck_spec(
        self,
        *,
        outline: PPTOutline,
        settings: PPTRenderSettings,
        inputs: list[IngestEntry],
        document_summary: DocumentSummary | None = None,
        template_summary: TemplateSummary | None = None,
    ) -> DeckSpec:
        """Generate an executable deck spec from an approved PPT outline."""
        prompt = render_prompt_template(
            "deck_spec_instruction",
            {
                "outline": outline.model_dump_json(indent=2),
                "render_settings": settings.model_dump_json(indent=2),
                "input_context": _input_context_json(inputs, document_summary, template_summary),
            },
        )
        spec = await self._run_structured_agent(
            agent_name="PPTDeckSpecExpert",
            instruction=(
                "You convert approved presentation outlines into executable slide specifications. "
                "Return only fields requested by the schema."
            ),
            request_text=prompt,
            output_schema=_AdkDeckSpec,
            output_key="ppt_deck_spec",
        )
        return _to_deck_spec(spec, outline=outline, valid_source_refs=_valid_source_refs(inputs))

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
        user_id = "ppt-production"
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


def _to_ppt_outline(plan: _AdkPPTOutlinePlan, *, target_pages: int, valid_source_refs: set[str]) -> PPTOutline:
    """Convert strict expert output into the production outline model."""
    entries = sorted(plan.entries, key=lambda entry: entry.sequence_index or 0)
    if len(entries) != target_pages:
        raise PPTExpertRuntimeError(f"Outline expert returned {len(entries)} slide(s); expected {target_pages}.")
    outline_entries: list[PPTOutlineEntry] = []
    for index, entry in enumerate(entries, start=1):
        title = _clean_text(entry.title)
        purpose = _clean_text(entry.purpose)
        bullets = _clean_list(entry.bullet_points, limit=6)
        if not title or not bullets:
            raise PPTExpertRuntimeError(f"Outline expert returned incomplete slide {index}.")
        outline_entries.append(
            PPTOutlineEntry(
                sequence_index=index,
                title=title,
                purpose=purpose or "Present this part of the narrative clearly.",
                layout_type=_coerce_layout(entry.layout_type, index=index, total=target_pages),
                bullet_points=bullets,
                speaker_notes=_clean_text(entry.speaker_notes) or purpose,
                source_refs=_filter_source_refs(entry.source_refs, valid_source_refs),
            )
        )
    return PPTOutline(
        title=_clean_text(plan.title) or outline_entries[0].title,
        target_pages=target_pages,
        entries=outline_entries,
    )


def _to_deck_spec(spec: _AdkDeckSpec, *, outline: PPTOutline, valid_source_refs: set[str]) -> DeckSpec:
    """Convert strict expert output into the production deck spec model."""
    if len(spec.slides) != len(outline.entries):
        raise PPTExpertRuntimeError(f"Deck-spec expert returned {len(spec.slides)} slide(s); expected {len(outline.entries)}.")
    slides_by_sequence = {slide.sequence_index: slide for slide in spec.slides if slide.sequence_index}
    deck_slides: list[DeckSlide] = []
    for entry in outline.entries:
        slide = slides_by_sequence.get(entry.sequence_index)
        if slide is None:
            raise PPTExpertRuntimeError(f"Deck-spec expert did not return slide {entry.sequence_index}.")
        layout_type = _coerce_layout(slide.layout_type, index=entry.sequence_index, total=len(outline.entries))
        bullets = _clean_list(slide.bullets, limit=3 if layout_type == "metric" else 6)
        if not bullets:
            raise PPTExpertRuntimeError(f"Deck-spec expert returned no bullets for slide {entry.sequence_index}.")
        deck_slides.append(
            DeckSlide(
                slide_id=entry.slide_id,
                sequence_index=entry.sequence_index,
                title=_clean_text(slide.title) or entry.title,
                layout_type=layout_type,
                bullets=bullets,
                visual_notes=_clean_text(slide.visual_notes),
                speaker_notes=_clean_text(slide.speaker_notes) or entry.speaker_notes,
                source_refs=_filter_source_refs(slide.source_refs, valid_source_refs) or list(entry.source_refs),
            )
        )
    return DeckSpec(title=_clean_text(spec.title) or outline.title, slides=deck_slides)


def _input_context_json(
    inputs: list[IngestEntry],
    document_summary: DocumentSummary | None,
    template_summary: TemplateSummary | None,
) -> str:
    """Return compact JSON context for PPT planning prompts."""
    payload = {
        "inputs": [item.model_dump(mode="json") for item in inputs],
        "reference_images": _reference_image_context(inputs),
        "document_summary": document_summary.model_dump(mode="json") if document_summary is not None else None,
        "template_summary": template_summary.model_dump(mode="json") if template_summary is not None else None,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _reference_image_context(inputs: list[IngestEntry]) -> list[dict[str, str]]:
    """Return lightweight visual context from reference-image input metadata."""
    references: list[dict[str, str]] = []
    for item in inputs:
        if item.role != "reference_image":
            continue
        references.append(
            {
                "input_id": item.input_id,
                "name": item.name,
                "path": item.path,
                "description": str(item.metadata.get("description", "") or ""),
                "status": item.status,
            }
        )
    return references


def _valid_source_refs(inputs: list[IngestEntry]) -> set[str]:
    """Return input ids that a generated slide may cite as source refs."""
    return {item.input_id for item in inputs if item.role == "source_doc"}


def _filter_source_refs(source_refs: list[str], valid_source_refs: set[str]) -> list[str]:
    """Keep only valid source document input ids in stable order."""
    filtered: list[str] = []
    for source_ref in source_refs:
        normalized = str(source_ref or "").strip()
        if normalized and normalized in valid_source_refs and normalized not in filtered:
            filtered.append(normalized)
    return filtered


def _coerce_layout(layout_type: str, *, index: int, total: int) -> PPTLayoutType:
    """Return one supported PPT layout type."""
    normalized = str(layout_type or "").strip().lower()
    if normalized in _ALLOWED_LAYOUTS:
        return normalized  # type: ignore[return-value]
    if index == 1:
        return "cover"
    if index == total:
        return "closing"
    return "content"


def _clean_text(value: object) -> str:
    """Normalize one model-produced text field."""
    return " ".join(str(value or "").split())


def _clean_list(values: list[str], *, limit: int) -> list[str]:
    """Normalize and bound model-produced text lists."""
    cleaned: list[str] = []
    for value in values:
        item = _clean_text(value)
        if item and item not in cleaned:
            cleaned.append(item)
        if len(cleaned) >= limit:
            break
    return cleaned


def _coerce_structured_output(raw_output: Any, output_schema: type[SchemaT]) -> SchemaT:
    """Coerce ADK structured output into a Pydantic model."""
    if isinstance(raw_output, output_schema):
        return raw_output
    if isinstance(raw_output, BaseModel):
        return output_schema.model_validate(raw_output.model_dump(mode="json"))
    if isinstance(raw_output, str):
        text = raw_output.strip()
        if not text:
            raise PPTExpertRuntimeError("PPT expert returned an empty response.")
        return output_schema.model_validate_json(text)
    return output_schema.model_validate(raw_output)
