"""Runtime service for PPT production."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from src.production.errors import ProductionError as ProductionRuntimeError
from src.production.errors import ProductionSessionNotFoundError
from src.production.models import (
    ProductionBreakpoint,
    ProductionErrorInfo,
    ProductionEvent,
    ProductionOwnerRef,
    ProductionRunResult,
    ReviewPayload,
    WorkspaceFileRef,
)
from src.production.ppt.deck_builder import DeckBuilderService
from src.production.ppt.document_loader import DocumentLoaderService
from src.production.ppt.impact import build_revision_impact_view
from src.production.ppt.ingest import ingest_input_files
from src.production.ppt.models import (
    DeckSlide,
    DeckSpec,
    DocumentSummary,
    FinalArtifact,
    PPTRenderSettings,
    PPTOutline,
    PPTOutlineEntry,
    PPTProductionState,
    SlidePreview,
    TemplateSummary,
)
from src.production.ppt.preview_renderer import PreviewRendererService
from src.production.ppt.template_analyzer import TemplateAnalyzerService
from src.production.ppt.quality import build_quality_report, quality_report_markdown
from src.production.ppt.user_response import normalize_user_response
from src.production.projection import get_active_production_session_id
from src.production.session_store import ProductionSessionStore
from src.runtime.step_events import publish_orchestration_step_event
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path


_VIEW_TYPES = (
    "overview",
    "brief",
    "inputs",
    "document_summary",
    "template_summary",
    "outline",
    "deck_spec",
    "previews",
    "quality",
    "events",
    "artifacts",
)


class PPTProductionManager:
    """Coordinate PPT production state, review checkpoints, rendering, and projection."""

    capability = "ppt"

    def __init__(
        self,
        *,
        store: ProductionSessionStore | None = None,
        deck_builder: DeckBuilderService | None = None,
        preview_renderer: PreviewRendererService | None = None,
        document_loader: DocumentLoaderService | None = None,
        template_analyzer: TemplateAnalyzerService | None = None,
    ) -> None:
        """Initialize the PPT production manager."""
        self.store = store or ProductionSessionStore()
        self.deck_builder = deck_builder or DeckBuilderService()
        self.preview_renderer = preview_renderer or PreviewRendererService()
        self.document_loader = document_loader or DocumentLoaderService()
        self.template_analyzer = template_analyzer or TemplateAnalyzerService()

    async def start(
        self,
        *,
        user_prompt: str,
        input_files: list[Any],
        placeholder_assets: bool,
        render_settings: dict[str, Any] | None,
        adk_state,
    ) -> ProductionRunResult:
        """Start a PPT production run and pause at outline review unless review is skipped."""
        context = _context_from_adk_state(adk_state)
        production_session = self.store.create_session(
            capability=self.capability,
            adk_session_id=context["sid"],
            turn_index=context["turn_index"],
            owner_ref=context["owner_ref"],
        )
        settings = _normalize_render_settings(render_settings or {})
        inputs = ingest_input_files(input_files, turn_index=context["turn_index"])
        template_summary = self.template_analyzer.build_summary(inputs)
        document_summary = self.document_loader.build_summary(inputs)
        state = PPTProductionState(
            production_session=production_session,
            status="running",
            stage="initializing",
            progress_percent=5,
            brief_summary=_brief_summary(user_prompt),
            render_settings=settings,
            inputs=inputs,
            template_summary=template_summary,
            document_summary=document_summary,
            warnings=_state_warnings(inputs, template_summary, document_summary),
        )
        state.production_events.append(
            ProductionEvent(
                event_type="production_started",
                stage=state.stage,
                message="PPT production session started.",
                metadata={"input_count": len(inputs), "settings": settings.model_dump(mode="json")},
            )
        )
        try:
            state.outline = _build_outline(
                brief=state.brief_summary,
                settings=settings,
                inputs=state.inputs,
                document_summary=state.document_summary,
                template_summary=state.template_summary,
            )
            state.stage = "outline_planning"
            state.progress_percent = 15
            if placeholder_assets or settings.skip_review:
                state.outline.status = "approved"
                return self._build_final_preview_or_complete(
                    state,
                    adk_state=adk_state,
                    complete=bool(placeholder_assets or settings.skip_review),
                    message_prefix="Generated PPT production output.",
                )

            state.status = "needs_user_review"
            state.stage = "outline_review"
            state.progress_percent = 20
            state.active_breakpoint = ProductionBreakpoint(
                stage=state.stage,
                review_payload=_outline_review_payload(state),
            )
            state.production_events.append(
                ProductionEvent(
                    event_type="outline_review_required",
                    stage=state.stage,
                    message="Prepared a PPT outline and paused before PPTX generation.",
                    metadata={"outline_id": state.outline.outline_id, "slide_count": len(state.outline.entries)},
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_pointer_to_adk_state(adk_state, state)
            return self._result_from_state(state, message="Please review the PPT outline before deck generation.")
        except Exception as exc:
            state.status = "failed"
            state.stage = "failed"
            state.production_events.append(
                ProductionEvent(
                    event_type="production_failed",
                    stage=state.stage,
                    message=f"PPT production failed: {type(exc).__name__}: {exc}",
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            return self._result_from_state(
                state,
                message="PPT production failed.",
                error=ProductionErrorInfo(code="ppt_start_failed", message=f"{type(exc).__name__}: {exc}"),
            )

    async def status(self, *, production_session_id: str | None, adk_state) -> ProductionRunResult:
        """Return a read-only status snapshot for a PPT production run."""
        state_or_result = self._load_state_or_not_found(production_session_id, adk_state)
        if isinstance(state_or_result, ProductionRunResult):
            return state_or_result
        return self._result_from_state(state_or_result, message=_status_message(state_or_result))

    async def view(self, *, production_session_id: str | None, view_type: str | None, adk_state) -> ProductionRunResult:
        """Return a read-only PPT production view derived from persisted state."""
        state_or_result = self._load_state_or_not_found(production_session_id, adk_state)
        if isinstance(state_or_result, ProductionRunResult):
            return state_or_result
        state = state_or_result
        normalized_view = _normalize_view_type(view_type)
        if normalized_view is None:
            return self._result_from_state(
                state,
                message=f"Unsupported PPT production view_type. Allowed: {', '.join(_VIEW_TYPES)}.",
                error=ProductionErrorInfo(
                    code="ppt_invalid_view_type",
                    message=f"Unsupported PPT production view_type. Allowed: {', '.join(_VIEW_TYPES)}.",
                ),
            )
        return self._result_from_state(
            state,
            message=f"Loaded PPT production view: {normalized_view}.",
            view=_build_production_view(state, normalized_view),
        )

    async def resume(self, *, production_session_id: str | None, user_response: Any | None, adk_state) -> ProductionRunResult:
        """Resume a PPT production session from an active review breakpoint."""
        try:
            state_or_result = self._load_state_or_not_found(production_session_id, adk_state)
            if isinstance(state_or_result, ProductionRunResult):
                return state_or_result
            state = state_or_result
        except ProductionRuntimeError:
            return await self.status(production_session_id=production_session_id, adk_state=adk_state)

        response = normalize_user_response(user_response)
        decision = _normalize_resume_decision(response)
        if state.active_breakpoint is None:
            state.production_events.append(
                ProductionEvent(
                    event_type="resume_ignored",
                    stage=state.stage,
                    message="Resume ignored because there is no active PPT production breakpoint.",
                    metadata={"user_response": response},
                )
            )
            self.store.save_state(state)
            return self._result_from_state(state, message="There is no active PPT review breakpoint to resume.")

        if decision == "cancel":
            state.status = "cancelled"
            state.stage = "cancelled"
            state.active_breakpoint = None
            state.production_events.append(
                ProductionEvent(
                    event_type="production_cancelled",
                    stage=state.stage,
                    message="User cancelled PPT production.",
                    metadata={"user_response": response},
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_pointer_to_adk_state(adk_state, state)
            return self._result_from_state(state, message="PPT production was cancelled.")

        if decision == "revise":
            return self._revise_active_breakpoint_and_pause(state, user_response=response, adk_state=adk_state)

        if decision != "approve":
            state.production_events.append(
                ProductionEvent(
                    event_type="resume_decision_required",
                    stage=state.stage,
                    message="Resume requires decision=approve, revise, or cancel.",
                    metadata={"user_response": response},
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_pointer_to_adk_state(adk_state, state)
            return self._result_from_state(state, message="Please respond with decision=approve, revise, or cancel.")

        if state.active_breakpoint.stage == "outline_review":
            if state.outline is not None:
                state.outline.status = "approved"
            state.active_breakpoint = None
            if _should_pause_for_deck_spec_review(state):
                return self._build_deck_spec_and_pause(state, adk_state=adk_state)
            return self._build_final_preview_or_complete(
                state,
                adk_state=adk_state,
                complete=False,
                message_prefix="Outline approved.",
            )
        if state.active_breakpoint.stage == "deck_spec_review":
            _approve_deck_spec(state.deck_spec)
            state.active_breakpoint = None
            return self._build_final_preview_or_complete(
                state,
                adk_state=adk_state,
                complete=False,
                message_prefix="Deck spec approved.",
            )
        if state.active_breakpoint.stage == "final_preview_review":
            state.status = "completed"
            state.stage = "completed"
            state.progress_percent = 100
            state.active_breakpoint = None
            for preview in state.slide_previews:
                preview.status = "approved"
            state.production_events.append(
                ProductionEvent(
                    event_type="production_completed",
                    stage=state.stage,
                    message="User approved the PPT preview; production completed.",
                    metadata={"artifact_count": len(state.artifacts)},
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_to_adk_state(adk_state, state)
            return self._result_from_state(state, message="PPT production completed.")

        return self._result_from_state(
            state,
            message=f"Current PPT review stage cannot be approved by this action: {state.active_breakpoint.stage}.",
            error=ProductionErrorInfo(code="ppt_resume_stage_unsupported", message="Unsupported PPT review stage."),
        )

    async def add_inputs(
        self,
        *,
        production_session_id: str | None,
        input_files: list[Any],
        user_response: Any | None,
        adk_state,
    ) -> ProductionRunResult:
        """Add template, source document, or reference-image inputs to an existing PPT session."""
        context = _context_from_adk_state(adk_state)
        state_or_result = self._load_state_or_not_found(production_session_id, adk_state)
        if isinstance(state_or_result, ProductionRunResult):
            return state_or_result
        state = state_or_result
        new_inputs = ingest_input_files(input_files, turn_index=context["turn_index"])
        if not new_inputs:
            return self._result_from_state(
                state,
                message="No valid PPT inputs were provided.",
                error=ProductionErrorInfo(code="ppt_invalid_input", message="No valid PPT inputs were provided."),
            )
        state.inputs.extend(new_inputs)
        state.template_summary = self.template_analyzer.build_summary(state.inputs)
        state.document_summary = self.document_loader.build_summary(state.inputs)
        state.warnings = _state_warnings(state.inputs, state.template_summary, state.document_summary)
        state.stale_items = ["outline", "deck_spec", "slide_previews", "final", "quality"]
        state.outline = _build_outline(
            brief=state.brief_summary,
            settings=state.render_settings,
            inputs=state.inputs,
            document_summary=state.document_summary,
            template_summary=state.template_summary,
        )
        state.deck_spec = None
        state.slide_previews = []
        state.final_artifact = None
        state.quality_report = None
        state.artifacts = []
        state.status = "needs_user_review"
        state.stage = "outline_review"
        state.progress_percent = max(state.progress_percent, 20)
        state.active_breakpoint = ProductionBreakpoint(stage=state.stage, review_payload=_outline_review_payload(state))
        state.production_events.append(
            ProductionEvent(
                event_type="ppt_inputs_added",
                stage=state.stage,
                message="Added PPT inputs and returned to outline review.",
                metadata={"added_input_ids": [item.input_id for item in new_inputs], "user_response": normalize_user_response(user_response)},
            )
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_pointer_to_adk_state(adk_state, state)
        return self._result_from_state(state, message=f"Added {len(new_inputs)} PPT input(s). Please review the updated outline.")

    async def analyze_revision_impact(
        self,
        *,
        production_session_id: str | None,
        user_response: Any | None,
        adk_state,
    ) -> ProductionRunResult:
        """Return a read-only impact analysis for a requested PPT revision."""
        state_or_result = self._load_state_or_not_found(production_session_id, adk_state)
        if isinstance(state_or_result, ProductionRunResult):
            return state_or_result
        state = state_or_result
        return self._result_from_state(
            state,
            message="Loaded PPT revision impact analysis. No production state was changed.",
            view=build_revision_impact_view(state, user_response),
        )

    async def apply_revision(
        self,
        *,
        production_session_id: str | None,
        user_response: Any | None,
        adk_state,
    ) -> ProductionRunResult:
        """Apply a confirmed PPT revision and pause at outline review."""
        state_or_result = self._load_state_or_not_found(production_session_id, adk_state)
        if isinstance(state_or_result, ProductionRunResult):
            return state_or_result
        state = state_or_result
        response = normalize_user_response(user_response)
        notes = _revision_notes_from_response(response)
        impact_view = build_revision_impact_view(state, response)
        if not notes:
            return self._result_from_state(
                state,
                message="Please provide concrete revision notes before applying a PPT change.",
                view=impact_view,
                error=ProductionErrorInfo(code="ppt_invalid_revision_request", message="Revision notes are required."),
            )
        if impact_view.get("unmatched_targets") and not impact_view.get("matched_targets"):
            return self._result_from_state(
                state,
                message="Revision target was not found. Choose one available target before applying the change.",
                view=impact_view,
                error=ProductionErrorInfo(code="ppt_revision_target_unmatched", message="Revision target was not found."),
            )
        return self._apply_revision_by_impact(state, notes=notes, user_response=response, impact_view=impact_view, adk_state=adk_state)

    async def regenerate_stale_segments(self, *, production_session_id: str | None, adk_state) -> ProductionRunResult:
        """Regenerate stale slide segment PPTX files and preview PNGs without rebuilding the final deck."""
        state_or_result = self._load_state_or_not_found(production_session_id, adk_state)
        if isinstance(state_or_result, ProductionRunResult):
            return state_or_result
        state = state_or_result
        if state.deck_spec is None:
            return self._result_from_state(
                state,
                message="PPT deck spec is required before regenerating stale slide segments.",
                error=ProductionErrorInfo(
                    code="ppt_deck_spec_required",
                    message="PPT deck spec is required before regenerating stale slide segments.",
                ),
            )
        target_slide_ids = _stale_preview_slide_ids(state)
        if not target_slide_ids:
            return self._result_from_state(
                state,
                message="No stale PPT slide previews need regeneration.",
                view=_build_production_view(state, "previews"),
            )
        target_slides = _slides_matching_ids(state.deck_spec, target_slide_ids)
        if not target_slides:
            return self._result_from_state(
                state,
                message="No matching stale PPT deck slides were found for regeneration.",
                error=ProductionErrorInfo(
                    code="ppt_stale_slide_not_found",
                    message="No matching stale PPT deck slides were found for regeneration.",
                ),
            )
        try:
            session_root = self.store.session_root(state.production_session)
            state.stage = "slide_segment_regeneration"
            state.progress_percent = max(state.progress_percent, 74)
            _publish_ppt_progress(
                adk_state,
                state,
                title="Regenerating stale PPT slides",
                detail=f"Refreshing {len(target_slide_ids)} stale slide preview/segment artifact(s).",
            )
            regenerated_ids = self._regenerate_slide_segment_previews(
                state,
                slides=target_slides,
                session_root=session_root,
            )
            state.final_artifact = None
            state.quality_report = None
            state.artifacts = _artifact_refs(state)
            state.stale_items = _ensure_stale_items(
                _remove_slide_preview_stale_items(state.stale_items, set(regenerated_ids)),
                ["final", "quality"],
            )
            state.status = "needs_user_review"
            state.stage = "deck_spec_review"
            state.progress_percent = max(state.progress_percent, 76)
            state.active_breakpoint = ProductionBreakpoint(
                stage=state.stage,
                review_payload=_deck_spec_review_payload(state),
            )
            state.production_events.append(
                ProductionEvent(
                    event_type="ppt_stale_slide_segments_regenerated",
                    stage=state.stage,
                    message="Regenerated stale PPT slide segment artifacts and preview images.",
                    metadata={"slide_ids": regenerated_ids, "remaining_stale_items": state.stale_items},
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_pointer_to_adk_state(adk_state, state)
            return self._result_from_state(
                state,
                message=f"Regenerated {len(regenerated_ids)} stale PPT slide preview/segment artifact(s). Please review the updated deck spec.",
            )
        except Exception as exc:
            state.status = "failed"
            state.stage = "failed"
            state.production_events.append(
                ProductionEvent(
                    event_type="ppt_stale_slide_regeneration_failed",
                    stage=state.stage,
                    message=f"PPT stale slide regeneration failed: {type(exc).__name__}: {exc}",
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            return self._result_from_state(
                state,
                message="PPT stale slide regeneration failed.",
                error=ProductionErrorInfo(
                    code="ppt_stale_slide_regeneration_failed",
                    message=f"{type(exc).__name__}: {exc}",
                ),
            )

    def _regenerate_slide_segment_previews(
        self,
        state: PPTProductionState,
        *,
        slides: list[DeckSlide],
        session_root: Path,
    ) -> list[str]:
        regenerated_ids: list[str] = []
        for slide in slides:
            updated_preview = self._regenerate_slide_segment_preview(
                state,
                slide=slide,
                session_root=session_root,
            )
            _replace_preview_for_slide(state.slide_previews, updated_preview)
            regenerated_ids.append(slide.slide_id)
        return regenerated_ids

    def _regenerate_slide_segment_preview(
        self,
        state: PPTProductionState,
        *,
        slide: DeckSlide,
        session_root: Path,
    ) -> SlidePreview:
        segment_spec = DeckSpec(
            title=state.deck_spec.title if state.deck_spec else "PPT Slide Segment",
            slides=[slide.model_copy(deep=True)],
        )
        segment_paths = self.deck_builder.build_slide_segments(
            deck_spec=segment_spec,
            render_settings=state.render_settings,
            output_dir=session_root / "segments",
        )
        segment_path = segment_paths.get(slide.slide_id, "")
        temp_preview_dir = session_root / "preview" / "_stale_segments" / f"slide-{slide.sequence_index:02d}"
        shutil.rmtree(temp_preview_dir, ignore_errors=True)
        rendered_previews = self.preview_renderer.render(
            pptx_path=segment_path,
            deck_spec=segment_spec,
            render_settings=state.render_settings,
            output_dir=temp_preview_dir,
        )
        if not rendered_previews:
            raise RuntimeError(f"No preview was rendered for stale slide {slide.sequence_index}.")
        rendered_preview = rendered_previews[0]
        stable_preview_path = session_root / "preview" / f"slide-{slide.sequence_index:02d}.png"
        stable_preview_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(resolve_workspace_path(rendered_preview.preview_path), stable_preview_path)
        shutil.rmtree(temp_preview_dir, ignore_errors=True)

        existing = _preview_by_slide_id(state).get(slide.slide_id)
        metadata = dict(rendered_preview.metadata)
        metadata.update(
            {
                "regenerated_from": "stale_slide_segment",
                "temporary_preview_path": rendered_preview.preview_path,
            }
        )
        return SlidePreview(
            preview_id=existing.preview_id if existing is not None else rendered_preview.preview_id,
            slide_id=slide.slide_id,
            sequence_index=slide.sequence_index,
            preview_path=workspace_relative_path(stable_preview_path),
            segment_path=segment_path,
            status="generated",
            metadata=metadata,
        )

    def _load_state_or_not_found(self, production_session_id: str | None, adk_state) -> PPTProductionState | ProductionRunResult:
        context = _context_from_adk_state(adk_state)
        session_id = _resolve_requested_session_id(production_session_id, adk_state)
        try:
            return self.store.load_state(
                production_session_id=session_id,
                adk_session_id=context["sid"],
                owner_ref=context["owner_ref"],
                state_type=PPTProductionState,
                capability=self.capability,
            )
        except ProductionSessionNotFoundError:
            return ProductionRunResult(
                status="failed",
                capability=self.capability,
                production_session_id=session_id or "",
                stage="not_found",
                progress_percent=0,
                message="PPT production session was not found or is not owned by this conversation.",
                error=ProductionErrorInfo(
                    code="ppt_session_not_found_or_not_owned",
                    message="PPT production session was not found or is not owned by this conversation.",
                ),
            )

    def _revise_active_breakpoint_and_pause(self, state: PPTProductionState, *, user_response: dict[str, Any], adk_state) -> ProductionRunResult:
        notes = _revision_notes_from_response(user_response)
        if notes:
            impact_view = build_revision_impact_view(state, user_response)
            return self._apply_revision_by_impact(state, notes=notes, user_response=user_response, impact_view=impact_view, adk_state=adk_state)
        state.production_events.append(
            ProductionEvent(
                event_type="revision_notes_required",
                stage=state.stage,
                message="Revision requested without concrete notes.",
                metadata={"user_response": user_response},
            )
        )
        self.store.save_state(state)
        return self._result_from_state(state, message="Please provide revision notes for the PPT review.")

    def _apply_revision_by_impact(
        self,
        state: PPTProductionState,
        *,
        notes: str,
        user_response: dict[str, Any],
        impact_view: dict[str, Any],
        adk_state,
    ) -> ProductionRunResult:
        matched_targets = impact_view.get("matched_targets") or []
        if _has_only_target_kind(matched_targets, "deck_slide") and state.deck_spec is not None:
            return self._apply_deck_slide_revision_and_pause(state, notes=notes, matched_targets=matched_targets, user_response=user_response, impact_view=impact_view, adk_state=adk_state)
        if _has_only_target_kind(matched_targets, "outline_entry") and state.outline is not None:
            return self._apply_outline_entry_revision_and_pause(state, notes=notes, matched_targets=matched_targets, user_response=user_response, impact_view=impact_view, adk_state=adk_state)
        return self._apply_revision_notes_and_pause(state, notes=notes, user_response=user_response, adk_state=adk_state)

    def _apply_revision_notes_and_pause(self, state: PPTProductionState, *, notes: str, user_response: dict[str, Any], adk_state) -> ProductionRunResult:
        state.brief_summary = _append_revision_note(state.brief_summary, notes)
        state.outline = _build_outline(
            brief=state.brief_summary,
            settings=state.render_settings,
            inputs=state.inputs,
            document_summary=state.document_summary,
            template_summary=state.template_summary,
        )
        state.deck_spec = None
        state.slide_previews = []
        state.final_artifact = None
        state.quality_report = None
        state.artifacts = []
        state.stale_items = ["outline", "deck_spec", "slide_previews", "final", "quality"]
        state.revision_history.append({"notes": notes, "user_response": user_response})
        state.status = "needs_user_review"
        state.stage = "outline_review"
        state.progress_percent = max(20, min(state.progress_percent, 40))
        state.active_breakpoint = ProductionBreakpoint(stage=state.stage, review_payload=_outline_review_payload(state))
        state.production_events.append(
            ProductionEvent(
                event_type="ppt_revision_applied",
                stage=state.stage,
                message="Applied PPT revision notes and paused at outline review.",
                metadata={"user_response": user_response},
            )
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_pointer_to_adk_state(adk_state, state)
        return self._result_from_state(state, message="PPT revision was applied. Please review the updated outline.")

    def _apply_outline_entry_revision_and_pause(
        self,
        state: PPTProductionState,
        *,
        notes: str,
        matched_targets: list[dict[str, Any]],
        user_response: dict[str, Any],
        impact_view: dict[str, Any],
        adk_state,
    ) -> ProductionRunResult:
        target_ids = _target_ids(matched_targets, "outline_entry")
        for entry in state.outline.entries if state.outline is not None else []:
            if entry.slide_id in target_ids:
                _append_revision_to_outline_entry(entry, notes)
        state.deck_spec = None
        state.slide_previews = []
        state.final_artifact = None
        state.quality_report = None
        state.artifacts = []
        state.stale_items = impact_view.get("stale_items") or ["deck_spec", "slide_previews", "final", "quality"]
        state.revision_history.append({"notes": notes, "user_response": user_response, "matched_targets": matched_targets})
        state.status = "needs_user_review"
        state.stage = "outline_review"
        state.progress_percent = max(20, min(state.progress_percent, 45))
        state.active_breakpoint = ProductionBreakpoint(stage=state.stage, review_payload=_outline_review_payload(state))
        state.production_events.append(
            ProductionEvent(
                event_type="ppt_outline_entry_revision_applied",
                stage=state.stage,
                message="Applied targeted outline revision and paused at outline review.",
                metadata={"target_ids": target_ids, "user_response": user_response},
            )
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_pointer_to_adk_state(adk_state, state)
        return self._result_from_state(state, message="PPT outline revision was applied. Please review the updated outline.")

    def _apply_deck_slide_revision_and_pause(
        self,
        state: PPTProductionState,
        *,
        notes: str,
        matched_targets: list[dict[str, Any]],
        user_response: dict[str, Any],
        impact_view: dict[str, Any],
        adk_state,
    ) -> ProductionRunResult:
        target_ids = _target_ids(matched_targets, "deck_slide")
        for slide in state.deck_spec.slides if state.deck_spec is not None else []:
            if slide.slide_id in target_ids:
                _append_revision_to_deck_slide(slide, notes)
        if state.deck_spec is not None:
            state.deck_spec.status = "draft"
        _mark_target_previews_stale(state, target_ids)
        state.final_artifact = None
        state.quality_report = None
        state.artifacts = _artifact_refs(state)
        state.stale_items = impact_view.get("stale_items") or [f"slide_preview:{target_id}" for target_id in sorted(target_ids)] + ["final", "quality"]
        state.revision_history.append({"notes": notes, "user_response": user_response, "matched_targets": matched_targets})
        state.status = "needs_user_review"
        state.stage = "deck_spec_review"
        state.progress_percent = max(42, min(state.progress_percent, 70))
        state.active_breakpoint = ProductionBreakpoint(stage=state.stage, review_payload=_deck_spec_review_payload(state))
        state.production_events.append(
            ProductionEvent(
                event_type="ppt_deck_slide_revision_applied",
                stage=state.stage,
                message="Applied targeted deck slide revision and paused at deck spec review.",
                metadata={"target_ids": target_ids, "user_response": user_response},
            )
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_pointer_to_adk_state(adk_state, state)
        return self._result_from_state(state, message="PPT deck slide revision was applied. Please review the updated deck spec.")

    def _build_deck_spec_and_pause(self, state: PPTProductionState, *, adk_state) -> ProductionRunResult:
        """Build an executable deck spec and pause for user review."""
        try:
            state.stage = "deck_spec_planning"
            state.progress_percent = max(state.progress_percent, 35)
            _publish_ppt_progress(adk_state, state, title="Building deck spec", detail="Converting the approved outline into executable slide content.")
            state.deck_spec = _build_deck_spec(state.outline, state.render_settings)
            state.status = "needs_user_review"
            state.stage = "deck_spec_review"
            state.progress_percent = max(state.progress_percent, 42)
            state.active_breakpoint = ProductionBreakpoint(stage=state.stage, review_payload=_deck_spec_review_payload(state))
            state.production_events.append(
                ProductionEvent(
                    event_type="deck_spec_review_required",
                    stage=state.stage,
                    message="Prepared a PPT deck spec and paused before PPTX generation.",
                    metadata={"deck_spec_id": state.deck_spec.deck_spec_id, "slide_count": len(state.deck_spec.slides)},
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_pointer_to_adk_state(adk_state, state)
            return self._result_from_state(state, message="Please review the PPT deck spec before PPTX generation.")
        except Exception as exc:
            state.status = "failed"
            state.stage = "failed"
            state.production_events.append(
                ProductionEvent(
                    event_type="deck_spec_build_failed",
                    stage=state.stage,
                    message=f"Deck spec build failed: {type(exc).__name__}: {exc}",
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            return self._result_from_state(
                state,
                message="PPT deck spec build failed.",
                error=ProductionErrorInfo(code="ppt_deck_spec_build_failed", message=f"{type(exc).__name__}: {exc}"),
            )

    def _build_final_preview_or_complete(
        self,
        state: PPTProductionState,
        *,
        adk_state,
        complete: bool,
        message_prefix: str,
    ) -> ProductionRunResult:
        try:
            _publish_ppt_progress(adk_state, state, title="Building PPTX", detail="Generating editable PPTX from approved deck spec.")
            if state.deck_spec is None:
                state.deck_spec = _build_deck_spec(state.outline, state.render_settings)
            _approve_deck_spec(state.deck_spec)
            state.stage = "native_build"
            state.progress_percent = 55
            session_root = self.store.session_root(state.production_session)
            final_path = session_root / "final" / "final.pptx"
            pptx_path = self.deck_builder.build(
                deck_spec=state.deck_spec,
                render_settings=state.render_settings,
                output_path=final_path,
            )
            state.final_artifact = FinalArtifact(pptx_path=pptx_path)
            state.stage = "preview_rendering"
            state.progress_percent = 78
            state.slide_previews = self.preview_renderer.render(
                pptx_path=pptx_path,
                deck_spec=state.deck_spec,
                render_settings=state.render_settings,
                output_dir=session_root / "preview",
            )
            segment_paths = self.deck_builder.build_slide_segments(
                deck_spec=state.deck_spec,
                render_settings=state.render_settings,
                output_dir=session_root / "segments",
            )
            _attach_segment_paths(state.slide_previews, segment_paths)
            state.final_artifact.preview_paths = [item.preview_path for item in state.slide_previews]
            state.stage = "quality_check"
            state.progress_percent = 90
            quality_json_path = session_root / "quality_report.json"
            state.quality_report = build_quality_report(state, report_path=workspace_relative_path(quality_json_path))
            state.artifacts = _artifact_refs(state)
            state.stale_items = []
            if complete:
                state.status = "completed"
                state.stage = "completed"
                state.progress_percent = 100
                state.active_breakpoint = None
                event_type = "production_completed"
                message = f"{message_prefix} PPT production completed."
            else:
                state.status = "needs_user_review"
                state.stage = "final_preview_review"
                state.progress_percent = 95
                state.active_breakpoint = ProductionBreakpoint(stage=state.stage, review_payload=_final_preview_review_payload(state))
                event_type = "final_preview_review_required"
                message = f"{message_prefix} Please review the generated PPT previews before completion."
            state.production_events.append(
                ProductionEvent(
                    event_type=event_type,
                    stage=state.stage,
                    message=message,
                    metadata={"artifact_count": len(state.artifacts), "preview_count": len(state.slide_previews)},
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            if complete:
                self.store.project_to_adk_state(adk_state, state)
            else:
                self.store.project_pointer_to_adk_state(adk_state, state)
            return self._result_from_state(state, message=message)
        except Exception as exc:
            state.status = "failed"
            state.stage = "failed"
            state.production_events.append(
                ProductionEvent(
                    event_type="ppt_build_failed",
                    stage=state.stage,
                    message=f"PPT build failed: {type(exc).__name__}: {exc}",
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            return self._result_from_state(
                state,
                message="PPT build failed.",
                error=ProductionErrorInfo(code="ppt_deck_builder_failed", message=f"{type(exc).__name__}: {exc}"),
            )

    def _result_from_state(
        self,
        state: PPTProductionState,
        *,
        message: str,
        view: dict[str, Any] | None = None,
        error: ProductionErrorInfo | None = None,
    ) -> ProductionRunResult:
        return ProductionRunResult(
            status=state.status,
            capability=self.capability,
            production_session_id=state.production_session.production_session_id,
            stage=state.stage,
            progress_percent=state.progress_percent,
            message=message,
            state_ref=f"{state.production_session.root_dir}/state.json",
            artifacts=state.artifacts,
            review_payload=(state.active_breakpoint.review_payload if state.active_breakpoint is not None else None),
            view=view or {},
            error=error,
            events=state.production_events[-5:],
        )

    def _save_projection_files(self, state: PPTProductionState) -> None:
        """Write human-readable projection files derived from PPTProductionState."""
        root = self.store.session_root(state.production_session)
        (root / "brief.md").write_text(f"# PPT Brief\n\n{state.brief_summary}\n", encoding="utf-8")
        (root / "inputs.json").write_text(
            json.dumps([item.model_dump(mode="json") for item in state.inputs], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (root / "outline.json").write_text(
            json.dumps(
                {
                    "outline": state.outline.model_dump(mode="json") if state.outline is not None else None,
                    "active_review": state.active_breakpoint.review_payload.model_dump(mode="json") if state.active_breakpoint is not None else None,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (root / "outline.md").write_text(_outline_markdown(state), encoding="utf-8")
        (root / "deck_spec.json").write_text(
            json.dumps(state.deck_spec.model_dump(mode="json") if state.deck_spec is not None else None, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (root / "deck_spec.md").write_text(_deck_spec_markdown(state), encoding="utf-8")
        if state.template_summary is not None:
            (root / "template_summary.json").write_text(state.template_summary.model_dump_json(indent=2), encoding="utf-8")
            (root / "template_summary.md").write_text(_template_summary_markdown(state.template_summary), encoding="utf-8")
        if state.document_summary is not None:
            (root / "document_summary.json").write_text(state.document_summary.model_dump_json(indent=2), encoding="utf-8")
            (root / "document_summary.md").write_text(_document_summary_markdown(state.document_summary), encoding="utf-8")
        (root / "preview").mkdir(parents=True, exist_ok=True)
        (root / "preview" / "index.json").write_text(
            json.dumps([item.model_dump(mode="json") for item in state.slide_previews], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if state.quality_report is not None:
            (root / "quality_report.json").write_text(state.quality_report.model_dump_json(indent=2), encoding="utf-8")
        (root / "quality_report.md").write_text(quality_report_markdown(state.quality_report), encoding="utf-8")


def _context_from_adk_state(adk_state) -> dict[str, Any]:
    sid = str(adk_state.get("sid", "") or "").strip() or "default"
    turn_index = int(adk_state.get("turn_index", 0) or 0)
    return {
        "sid": sid,
        "turn_index": turn_index,
        "owner_ref": ProductionOwnerRef(
            channel=str(adk_state.get("channel", "") or "").strip(),
            chat_id=str(adk_state.get("chat_id", "") or "").strip(),
            sender_id=str(adk_state.get("sender_id", "") or "").strip(),
        ),
    }


def _normalize_render_settings(payload: dict[str, Any]) -> PPTRenderSettings:
    target_pages = _positive_int(payload.get("target_pages") or payload.get("pages"), default=6)
    target_pages = max(1, min(target_pages, 30))
    aspect_ratio = str(payload.get("aspect_ratio", "16:9") or "16:9").strip()
    if aspect_ratio not in {"16:9", "4:3", "9:16"}:
        aspect_ratio = "16:9"
    style = str(payload.get("style_preset", "business_executive") or "business_executive").strip()
    if style not in {"business_executive", "pitch_deck", "educational", "editorial_visual"}:
        style = "business_executive"
    pipeline = str(payload.get("pipeline", "auto") or "auto").strip()
    if pipeline not in {"auto", "native", "template", "html_deck"}:
        pipeline = "auto"
    return PPTRenderSettings(
        target_pages=target_pages,
        aspect_ratio=aspect_ratio,  # type: ignore[arg-type]
        style_preset=style,  # type: ignore[arg-type]
        pipeline=pipeline,  # type: ignore[arg-type]
        template_edit_mode=str(payload.get("template_edit_mode", "auto") or "auto").strip() or "auto",
        deck_spec_review=bool(payload.get("deck_spec_review", True)),
        skip_review=bool(payload.get("skip_review", False)),
    )


def _state_warnings(inputs, template_summary: TemplateSummary | None, document_summary: DocumentSummary | None) -> list[str]:
    warnings = _input_warnings(inputs)
    for summary in (template_summary, document_summary):
        if summary is None:
            continue
        for warning in summary.warnings:
            if warning and warning not in warnings:
                warnings.append(warning)
    return warnings


def _input_warnings(inputs) -> list[str]:
    warnings = [item.warning for item in inputs if item.warning]
    return [warning for warning in warnings if warning]


def _build_outline(
    *,
    brief: str,
    settings: PPTRenderSettings,
    inputs,
    document_summary: DocumentSummary | None = None,
    template_summary: TemplateSummary | None = None,
) -> PPTOutline:
    title = _deck_title(brief)
    count = settings.target_pages
    entries: list[PPTOutlineEntry] = []
    if count == 1:
        entries.append(_outline_entry(1, title, "Frame the full presentation in one executive summary slide.", "cover", [_one_line_summary(brief)]))
    else:
        entries.append(_outline_entry(1, title, "Open with the core message and audience context.", "cover", [_one_line_summary(brief)]))
        internal_count = max(0, count - 2)
        topic_plan = _topic_plan(brief, settings.style_preset, internal_count)
        for index, topic in enumerate(topic_plan, start=2):
            entries.append(
                _outline_entry(
                    index,
                    topic["title"],
                    topic["purpose"],
                    topic["layout_type"],
                    topic["bullets"],
                )
            )
        entries.append(
            _outline_entry(
                count,
                "Decision and Next Steps",
                "Close with the decision, owners, and immediate next actions.",
                "closing",
                ["Confirm the main decision or desired audience response.", "Assign owners for the next phase.", "Set a clear follow-up checkpoint."],
            )
        )
    _apply_input_context_to_outline(
        entries,
        inputs=inputs,
        document_summary=document_summary,
        template_summary=template_summary,
    )
    return PPTOutline(title=title, target_pages=count, entries=entries)


def _apply_input_context_to_outline(
    entries: list[PPTOutlineEntry],
    *,
    inputs,
    document_summary: DocumentSummary | None,
    template_summary: TemplateSummary | None,
) -> None:
    """Attach extracted input context to the deterministic outline."""
    if not entries:
        return
    if template_summary is not None and template_summary.status == "ready":
        entries[0].bullet_points.append(
            f"Template analyzed: {template_summary.layout_count} layout(s), "
            f"{template_summary.slide_count} slide(s); native editable output remains active."
        )
    elif any(item.role == "template_pptx" for item in inputs):
        entries[0].bullet_points.append("Template input is recorded; native editable output remains active.")

    if document_summary is not None and document_summary.status == "ready":
        entries[0].bullet_points.append("Source documents analyzed; outline includes extracted facts.")
        targets = entries[1:-1] or entries
        for index, fact in enumerate(document_summary.salient_facts[: max(1, min(4, len(targets)))]):
            target = targets[index % len(targets)]
            target.bullet_points.append(f"Source fact: {fact}")
            for source_id in document_summary.source_input_ids:
                if source_id not in target.source_refs:
                    target.source_refs.append(source_id)
    elif any(item.role == "source_doc" for item in inputs):
        entries[0].bullet_points.append("Source documents are attached, but no supported text was extracted yet.")


def _has_only_target_kind(matched_targets: list[dict[str, Any]], kind: str) -> bool:
    kinds = {str(item.get("kind", "") or "") for item in matched_targets}
    return bool(kinds) and kinds == {kind}


def _target_ids(matched_targets: list[dict[str, Any]], kind: str) -> set[str]:
    return {str(item.get("id", "") or "") for item in matched_targets if item.get("kind") == kind and item.get("id")}


def _append_revision_to_outline_entry(entry: PPTOutlineEntry, notes: str) -> None:
    bullet = _revision_bullet(notes)
    if bullet not in entry.bullet_points:
        entry.bullet_points.append(bullet)
    entry.speaker_notes = _append_revision_note(entry.speaker_notes, notes)
    entry.status = "draft"


def _append_revision_to_deck_slide(slide: DeckSlide, notes: str) -> None:
    bullet = _revision_bullet(notes)
    if bullet not in slide.bullets:
        slide.bullets.append(bullet)
    slide.speaker_notes = _append_revision_note(slide.speaker_notes, notes)
    slide.status = "draft"


def _revision_bullet(notes: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(notes or "").strip())
    if len(cleaned) > 180:
        cleaned = cleaned[:177].rstrip() + "..."
    return f"Revision note: {cleaned or 'Update requested.'}"


def _mark_target_previews_stale(state: PPTProductionState, target_ids: set[str]) -> None:
    for preview in state.slide_previews:
        if preview.slide_id in target_ids:
            preview.status = "stale"


def _attach_segment_paths(previews: list[SlidePreview], segment_paths: dict[str, str]) -> None:
    for preview in previews:
        preview.segment_path = segment_paths.get(preview.slide_id, "")


def _preview_by_slide_id(state: PPTProductionState) -> dict[str, SlidePreview]:
    return {preview.slide_id: preview for preview in state.slide_previews}


def _replace_preview_for_slide(previews: list[SlidePreview], updated_preview: SlidePreview) -> None:
    for index, preview in enumerate(previews):
        if preview.slide_id == updated_preview.slide_id:
            previews[index] = updated_preview
            return
    previews.append(updated_preview)
    previews.sort(key=lambda preview: preview.sequence_index)


def _stale_preview_slide_ids(state: PPTProductionState) -> set[str]:
    slide_ids = {preview.slide_id for preview in state.slide_previews if preview.status == "stale"}
    for item in state.stale_items:
        prefix, _, identifier = str(item).partition(":")
        if prefix == "slide_preview" and identifier:
            slide_ids.add(identifier)
    return slide_ids


def _slides_matching_ids(deck_spec: DeckSpec, slide_ids: set[str]) -> list[DeckSlide]:
    return [slide for slide in deck_spec.slides if slide.slide_id in slide_ids]


def _remove_slide_preview_stale_items(stale_items: list[str], regenerated_slide_ids: set[str]) -> list[str]:
    stale_preview_markers = {f"slide_preview:{slide_id}" for slide_id in regenerated_slide_ids}
    return [item for item in stale_items if item not in stale_preview_markers]


def _ensure_stale_items(stale_items: list[str], required_items: list[str]) -> list[str]:
    merged = list(stale_items)
    for item in required_items:
        if item not in merged:
            merged.append(item)
    return merged


def _should_pause_for_deck_spec_review(state: PPTProductionState) -> bool:
    return bool(state.render_settings.deck_spec_review and not state.render_settings.skip_review)


def _approve_deck_spec(deck_spec: DeckSpec | None) -> None:
    if deck_spec is None:
        return
    deck_spec.status = "approved"
    for slide in deck_spec.slides:
        slide.status = "approved"


def _outline_entry(sequence_index: int, title: str, purpose: str, layout_type: str, bullets: list[str]) -> PPTOutlineEntry:
    return PPTOutlineEntry(
        sequence_index=sequence_index,
        title=title,
        purpose=purpose,
        layout_type=layout_type,  # type: ignore[arg-type]
        bullet_points=bullets,
        speaker_notes=purpose,
    )


def _topic_plan(brief: str, style: str, count: int) -> list[dict[str, Any]]:
    base_by_style = {
        "pitch_deck": [
            ("Problem Worth Solving", "Clarify the pain point and why now.", "two_column"),
            ("Proposed Solution", "Show how the product or idea resolves the pain.", "content"),
            ("Market and Momentum", "Translate opportunity into evidence and urgency.", "metric"),
            ("Execution Plan", "Explain how the team will deliver.", "content"),
            ("Ask", "Make the requested support or decision explicit.", "two_column"),
        ],
        "educational": [
            ("Learning Objective", "Define what the audience should understand.", "content"),
            ("Key Concept", "Introduce the main mental model.", "two_column"),
            ("Worked Example", "Make the idea concrete with a sample case.", "content"),
            ("Common Pitfalls", "Show what to avoid and why.", "metric"),
            ("Practice Prompt", "Give the audience a way to apply the concept.", "content"),
        ],
        "editorial_visual": [
            ("Thesis", "State the editorial point of view.", "two_column"),
            ("Signal", "Show the observable evidence behind the thesis.", "metric"),
            ("Tension", "Explain the tradeoff or conflict.", "content"),
            ("Implication", "Make the consequence memorable.", "two_column"),
            ("Action", "Turn the point of view into movement.", "content"),
        ],
        "business_executive": [
            ("Context", "Align on business context and decision frame.", "content"),
            ("Current Signal", "Summarize what changed and why it matters.", "metric"),
            ("Options", "Compare practical choices for the audience.", "two_column"),
            ("Recommended Path", "Present the preferred direction and rationale.", "content"),
            ("Risks and Controls", "Name risks and mitigation owners.", "content"),
        ],
    }
    base = base_by_style.get(style, base_by_style["business_executive"])
    keywords = _keywords(brief)
    topics: list[dict[str, Any]] = []
    for index in range(count):
        title, purpose, layout_type = base[index % len(base)]
        if index >= len(base):
            title = f"{title} {index + 1}"
        topics.append(
            {
                "title": title,
                "purpose": purpose,
                "layout_type": layout_type,
                "bullets": _bullets_for(title, brief, keywords),
            }
        )
    return topics


def _bullets_for(title: str, brief: str, keywords: list[str]) -> list[str]:
    keyword_text = ", ".join(keywords[:3]) if keywords else "the user brief"
    return [
        f"Anchor this slide in {keyword_text}.",
        f"Explain the practical meaning of {title.lower()} for the target audience.",
        "Use one concrete proof point, example, or decision implication.",
    ]


def _build_deck_spec(outline: PPTOutline | None, settings: PPTRenderSettings) -> DeckSpec:
    if outline is None:
        raise ValueError("PPT outline is required before building deck spec.")
    slides = [
        DeckSlide(
            slide_id=entry.slide_id,
            sequence_index=entry.sequence_index,
            title=entry.title,
            layout_type=entry.layout_type,
            bullets=entry.bullet_points,
            visual_notes=_visual_note(entry.layout_type, settings.style_preset),
            speaker_notes=entry.speaker_notes,
        )
        for entry in outline.entries
    ]
    return DeckSpec(title=outline.title, slides=slides)


def _visual_note(layout_type: str, style: str) -> str:
    if layout_type == "metric":
        return "Large numbered cards with concise labels."
    if layout_type == "two_column":
        return "Two-column comparison with a strong visual block."
    if layout_type == "cover":
        return f"Bold {style.replace('_', ' ')} cover treatment."
    return "Editable native PPT shapes and text."


def _artifact_refs(state: PPTProductionState) -> list[WorkspaceFileRef]:
    artifacts: list[WorkspaceFileRef] = []
    if state.final_artifact and state.final_artifact.pptx_path:
        artifacts.append(
            WorkspaceFileRef(
                name="final.pptx",
                path=state.final_artifact.pptx_path,
                description="Editable PPTX generated by PPT production.",
                source="ppt",
            )
        )
    artifacts.extend(
        WorkspaceFileRef(
            name=Path(preview.preview_path).name,
            path=preview.preview_path,
            description=f"{preview.status.title()} preview image for slide {preview.sequence_index}.",
            source="ppt",
        )
        for preview in state.slide_previews
    )
    if state.quality_report is not None:
        root = state.production_session.root_dir
        artifacts.append(
            WorkspaceFileRef(
                name="quality_report.md",
                path=f"{root}/quality_report.md",
                description="Deterministic PPT quality report.",
                source="ppt",
            )
        )
    return artifacts


def _outline_review_payload(state: PPTProductionState) -> ReviewPayload:
    outline = state.outline
    items = []
    if outline is not None:
        items = [
            {
                "id": entry.slide_id,
                "sequence_index": entry.sequence_index,
                "title": entry.title,
                "purpose": entry.purpose,
                "layout_type": entry.layout_type,
                "bullet_points": entry.bullet_points,
            }
            for entry in outline.entries
        ]
    return ReviewPayload(
        review_type="ppt_outline_review",
        title="Review PPT Outline",
        summary="Approve this outline to build an executable deck spec, or revise it before generation.",
        items=items,
        options=[
            {"decision": "approve", "label": "Approve outline and build deck spec"},
            {"decision": "revise", "label": "Revise outline", "requires_notes": True},
            {"decision": "cancel", "label": "Cancel production"},
        ],
    )


def _deck_spec_review_payload(state: PPTProductionState) -> ReviewPayload:
    deck_spec = state.deck_spec
    items = []
    preview_by_slide_id = _preview_by_slide_id(state)
    if deck_spec is not None:
        items = []
        for slide in deck_spec.slides:
            preview = preview_by_slide_id.get(slide.slide_id)
            items.append(
                {
                    "id": slide.slide_id,
                    "sequence_index": slide.sequence_index,
                    "title": slide.title,
                    "layout_type": slide.layout_type,
                    "bullets": slide.bullets,
                    "visual_notes": slide.visual_notes,
                    "speaker_notes": slide.speaker_notes,
                    "status": slide.status,
                    "preview_status": preview.status if preview is not None else "",
                    "preview_path": preview.preview_path if preview is not None else "",
                    "segment_path": preview.segment_path if preview is not None else "",
                }
            )
    return ReviewPayload(
        review_type="ppt_deck_spec_review",
        title="Review PPT Deck Spec",
        summary="Approve this executable deck spec to generate an editable PPTX, or revise it before generation.",
        items=items,
        options=[
            {"decision": "approve", "label": "Approve deck spec and generate PPTX"},
            {"decision": "revise", "label": "Revise deck spec", "requires_notes": True},
            {"decision": "cancel", "label": "Cancel production"},
        ],
    )


def _final_preview_review_payload(state: PPTProductionState) -> ReviewPayload:
    return ReviewPayload(
        review_type="ppt_final_preview_review",
        title="Review Generated PPT Preview",
        summary="Approve the preview to complete production, or revise to return to outline planning.",
        items=[preview.model_dump(mode="json") for preview in state.slide_previews],
        options=[
            {"decision": "approve", "label": "Approve final PPTX"},
            {"decision": "revise", "label": "Revise deck", "requires_notes": True},
            {"decision": "cancel", "label": "Cancel production"},
        ],
    )


def _build_production_view(state: PPTProductionState, view_type: str) -> dict[str, Any]:
    base = _base_view(state, view_type)
    if view_type == "overview":
        base.update(
            {
                "brief_summary": state.brief_summary,
                "active_review": state.active_breakpoint.review_payload.model_dump(mode="json") if state.active_breakpoint is not None else None,
                "counts": {
                    "inputs": len(state.inputs),
                    "outline_slides": len(state.outline.entries) if state.outline is not None else 0,
                    "deck_spec_slides": len(state.deck_spec.slides) if state.deck_spec is not None else 0,
                    "previews": len(state.slide_previews),
                    "stale_previews": len([item for item in state.slide_previews if item.status == "stale"]),
                    "artifacts": len(state.artifacts),
                    "events": len(state.production_events),
                },
                "warnings": state.warnings,
                "artifacts": [item.model_dump(mode="json") for item in state.artifacts],
            }
        )
    elif view_type == "brief":
        base.update({"brief_summary": state.brief_summary, "brief_path": f"{state.production_session.root_dir}/brief.md"})
    elif view_type == "inputs":
        base.update({"inputs": [item.model_dump(mode="json") for item in state.inputs], "inputs_path": f"{state.production_session.root_dir}/inputs.json"})
    elif view_type == "document_summary":
        base.update(
            {
                "document_summary": state.document_summary.model_dump(mode="json") if state.document_summary else None,
                "document_summary_path": f"{state.production_session.root_dir}/document_summary.json",
                "document_summary_markdown_path": f"{state.production_session.root_dir}/document_summary.md",
            }
        )
    elif view_type == "template_summary":
        base.update(
            {
                "template_summary": state.template_summary.model_dump(mode="json") if state.template_summary else None,
                "template_summary_path": f"{state.production_session.root_dir}/template_summary.json",
                "template_summary_markdown_path": f"{state.production_session.root_dir}/template_summary.md",
            }
        )
    elif view_type == "outline":
        base.update({"outline": state.outline.model_dump(mode="json") if state.outline else None, "outline_path": f"{state.production_session.root_dir}/outline.json", "outline_markdown_path": f"{state.production_session.root_dir}/outline.md"})
    elif view_type == "deck_spec":
        base.update({"deck_spec": state.deck_spec.model_dump(mode="json") if state.deck_spec else None, "deck_spec_path": f"{state.production_session.root_dir}/deck_spec.json"})
    elif view_type == "previews":
        base.update({"previews": [item.model_dump(mode="json") for item in state.slide_previews], "preview_index_path": f"{state.production_session.root_dir}/preview/index.json"})
    elif view_type == "quality":
        base.update({"quality_report": state.quality_report.model_dump(mode="json") if state.quality_report else None, "quality_report_path": f"{state.production_session.root_dir}/quality_report.json", "quality_report_markdown_path": f"{state.production_session.root_dir}/quality_report.md"})
    elif view_type == "events":
        base.update({"events": [item.model_dump(mode="json") for item in state.production_events], "events_path": f"{state.production_session.root_dir}/events.jsonl"})
    elif view_type == "artifacts":
        base.update({"artifacts": [item.model_dump(mode="json") for item in state.artifacts], "final_dir": f"{state.production_session.root_dir}/final"})
    return base


def _base_view(state: PPTProductionState, view_type: str) -> dict[str, Any]:
    return {
        "view_type": view_type,
        "production_session_id": state.production_session.production_session_id,
        "capability": state.production_session.capability,
        "status": state.status,
        "stage": state.stage,
        "progress_percent": state.progress_percent,
        "state_ref": f"{state.production_session.root_dir}/state.json",
        "project_root": state.production_session.root_dir,
        "stale_items": state.stale_items,
    }


def _document_summary_markdown(summary: DocumentSummary) -> str:
    lines = [
        "# PPT Document Summary",
        "",
        f"Status: {summary.status}",
        "",
        summary.summary or "No source-document summary is available.",
        "",
    ]
    if summary.salient_facts:
        lines.append("## Salient Facts")
        lines.append("")
        lines.extend(f"- {fact}" for fact in summary.salient_facts)
        lines.append("")
    if summary.warnings:
        lines.append("## Warnings")
        lines.append("")
        lines.extend(f"- {warning}" for warning in summary.warnings)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _template_summary_markdown(summary: TemplateSummary) -> str:
    lines = [
        "# PPT Template Summary",
        "",
        f"Status: {summary.status}",
        "",
        summary.summary or "No template summary is available.",
        "",
    ]
    lines.extend([
        "## Detected Structure",
        "",
        f"- Slides: {summary.slide_count}",
        f"- Layouts: {summary.layout_count}",
        f"- Masters: {summary.master_count}",
        f"- Themes: {summary.theme_count}",
        f"- Media assets: {summary.media_count}",
        "",
    ])
    if summary.detected_fonts:
        lines.append(f"Fonts: {', '.join(summary.detected_fonts)}")
        lines.append("")
    if summary.detected_colors:
        lines.append(f"Colors: {', '.join(summary.detected_colors)}")
        lines.append("")
    if summary.sample_text:
        lines.append("## Sample Text")
        lines.append("")
        lines.extend(f"- {item}" for item in summary.sample_text)
        lines.append("")
    if summary.warnings:
        lines.append("## Warnings")
        lines.append("")
        lines.extend(f"- {warning}" for warning in summary.warnings)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _outline_markdown(state: PPTProductionState) -> str:
    if state.outline is None:
        return "# PPT Outline\n\nNo outline has been generated yet.\n"
    lines = [f"# {state.outline.title}", ""]
    for entry in state.outline.entries:
        lines.extend([f"## {entry.sequence_index}. {entry.title}", "", f"Purpose: {entry.purpose}", ""])
        lines.extend(f"- {bullet}" for bullet in entry.bullet_points)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _deck_spec_markdown(state: PPTProductionState) -> str:
    if state.deck_spec is None:
        return "# PPT Deck Spec\n\nNo deck spec has been generated yet.\n"
    lines = [f"# {state.deck_spec.title}", ""]
    for slide in state.deck_spec.slides:
        lines.extend([f"## {slide.sequence_index}. {slide.title}", "", f"Layout: {slide.layout_type}", f"Visual: {slide.visual_notes}", ""])
        lines.extend(f"- {bullet}" for bullet in slide.bullets)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _brief_summary(user_prompt: str) -> str:
    prompt = str(user_prompt or "").strip()
    return prompt or "PPT production."


def _deck_title(brief: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(brief or "").strip())
    if not cleaned:
        return "CreativeClaw Presentation"
    for separator in ("。", ".", "\n", "；", ";"):
        if separator in cleaned:
            cleaned = cleaned.split(separator)[0]
            break
    cleaned = cleaned.strip(" ：:，,。.")
    if len(cleaned) > 58:
        cleaned = cleaned[:55].rstrip() + "..."
    return cleaned or "CreativeClaw Presentation"


def _one_line_summary(brief: str) -> str:
    title = _deck_title(brief)
    if title == "CreativeClaw Presentation":
        return "Turn the brief into a structured, reviewable presentation."
    return f"Translate `{title}` into a clear presentation narrative."


def _keywords(brief: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", brief)
    seen: set[str] = set()
    keywords: list[str] = []
    for word in words:
        normalized = word.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        keywords.append(word)
        if len(keywords) >= 6:
            break
    return keywords


def _append_revision_note(brief: str, notes: str) -> str:
    return f"{brief.strip()}\n\nRevision notes: {notes.strip()}".strip()


def _revision_notes_from_response(response: dict[str, Any]) -> str:
    return str(response.get("notes", "") or response.get("message", "") or response.get("revision", "") or "").strip()


def _normalize_resume_decision(response: dict[str, Any]) -> str:
    decision = str(response.get("decision", "") or response.get("action", "") or "").strip().lower()
    if decision in {"approve", "approved", "yes", "ok", "confirm", "确认", "同意", "可以"}:
        return "approve"
    if decision in {"cancel", "cancelled", "stop", "取消", "停止"}:
        return "cancel"
    if decision in {"revise", "revision", "edit", "修改", "调整"}:
        return "revise"
    return decision


def _resolve_requested_session_id(production_session_id: str | None, adk_state) -> str:
    requested = str(production_session_id or "").strip()
    if requested:
        return requested
    return get_active_production_session_id(adk_state, capability="ppt")


def _normalize_view_type(view_type: str | None) -> str | None:
    value = str(view_type or "overview").strip().lower() or "overview"
    return value if value in _VIEW_TYPES else None


def _positive_int(value: Any, default: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized > 0 else default


def _status_message(state: PPTProductionState) -> str:
    return (
        f"PPT production `{state.production_session.production_session_id}` "
        f"is {state.status} at stage `{state.stage}` ({state.progress_percent}%)."
    )


def _publish_ppt_progress(adk_state, state: PPTProductionState, *, title: str, detail: str) -> None:
    session_id = str(adk_state.get("sid", "") or "").strip()
    if not session_id:
        return
    publish_orchestration_step_event(
        session_id=session_id,
        turn_index=_normalize_turn_index(adk_state.get("turn_index")),
        title=title,
        detail=detail,
        stage=state.stage,
    )


def _normalize_turn_index(value: Any) -> int | None:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None
