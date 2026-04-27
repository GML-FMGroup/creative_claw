"""Runtime service for short-video production."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from src.agents.experts.video_generation.capabilities import (
    VIDEO_GENERATION_SEEDANCE_2_FAST_MODEL_NAME,
    VIDEO_GENERATION_VEO_MODEL_NAME,
    normalize_seedance_model_name,
    normalize_seedance_video_resolution,
)
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
    new_id,
)
from src.production.projection import get_active_production_session_id
from src.production.session_store import ProductionSessionStore
from src.production.short_video.models import (
    AssetManifestEntry,
    AudioClip,
    AudioTrack,
    ReferenceAssetEntry,
    ShortVideoAssetPlan,
    ShortVideoProductionState,
    ShortVideoQualityReport,
    ShortVideoRenderSettings,
    ShortVideoShotArtifact,
    ShortVideoShotAssetPlan,
    ShortVideoStoryboard,
    ShortVideoStoryboardShot,
    ShortVideoShotPlan,
    ShortVideoTimeline,
    VideoClip,
    VideoTrack,
)
from src.production.short_video.impact import build_revision_impact_view
from src.production.short_video.placeholders import PlaceholderAssetFactory
from src.production.short_video.prompt_catalog import render_prompt_template
from src.production.short_video.providers import (
    RoutedShortVideoProviderRuntime,
    ShortVideoProviderError,
    ShortVideoProviderRuntime,
)
from src.production.short_video.quality import build_quality_report, quality_report_markdown
from src.production.short_video.renderer import TimelineRenderer
from src.production.short_video.user_response import normalize_user_response
from src.production.short_video.validators import RenderValidator
from src.runtime.step_events import publish_orchestration_step_event
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path


_VIEW_TYPES = ("overview", "brief", "storyboard", "asset_plan", "timeline", "quality", "events", "artifacts")
_VIDEO_TYPES = ("product_ad", "cartoon_short_drama", "social_media_short")
_VIDEO_TYPE_LABELS = {
    "product_ad": "product-ad",
    "cartoon_short_drama": "cartoon short-drama",
    "social_media_short": "social-media short",
}


class ShortVideoProductionManager:
    """Coordinate short-video production state, rendering, and projection."""

    capability = "short_video"

    def __init__(
        self,
        *,
        store: ProductionSessionStore | None = None,
        placeholder_factory: PlaceholderAssetFactory | None = None,
        provider_runtime: ShortVideoProviderRuntime | None = None,
        renderer: TimelineRenderer | None = None,
        validator: RenderValidator | None = None,
    ) -> None:
        """Initialize the short-video production manager."""
        self.store = store or ProductionSessionStore()
        self.placeholder_factory = placeholder_factory or PlaceholderAssetFactory()
        self.provider_runtime = provider_runtime or RoutedShortVideoProviderRuntime()
        self.renderer = renderer or TimelineRenderer()
        self.validator = validator or RenderValidator()

    async def start(
        self,
        *,
        user_prompt: str,
        input_files: list[Any],
        placeholder_assets: bool,
        render_settings: dict[str, Any] | None,
        adk_state,
    ) -> ProductionRunResult:
        """Start a short-video production run or pause at the first P1a review."""
        context = _context_from_adk_state(adk_state)
        production_session = self.store.create_session(
            capability=self.capability,
            adk_session_id=context["sid"],
            turn_index=context["turn_index"],
            owner_ref=context["owner_ref"],
        )
        state = ShortVideoProductionState(
            production_session=production_session,
            status="running",
            stage="initializing",
            progress_percent=5,
            brief_summary=_brief_summary(user_prompt),
            reference_assets=_reference_assets_from_input_files(
                input_files=input_files,
                turn_index=context["turn_index"],
            ),
        )
        state.production_events.append(
            ProductionEvent(
                event_type="production_started",
                stage=state.stage,
                message="Short-video production session started.",
            )
        )

        try:
            if not placeholder_assets:
                return self._prepare_storyboard_review(
                    state,
                    user_prompt=user_prompt,
                    render_settings_payload=render_settings or {},
                    adk_state=adk_state,
                )

            session_root = self.store.session_root(production_session)
            settings = _normalize_render_settings(render_settings or {})
            duration_seconds = float((render_settings or {}).get("duration_seconds") or 4.0)
            state.asset_manifest, state.audio_manifest = self.placeholder_factory.create(
                session_root=session_root,
                render_settings=settings,
                duration_seconds=duration_seconds,
            )
            state.stage = "timeline_prepared"
            state.progress_percent = 45
            state.timeline = _build_placeholder_timeline(
                asset_id=_first_video_asset_id(state),
                audio_id=_first_audio_id(state),
                render_settings=settings,
                duration_seconds=duration_seconds,
            )
            state.production_events.append(
                ProductionEvent(
                    event_type="placeholder_assets_created",
                    stage=state.stage,
                    message="Created placeholder video, image, and silent audio assets.",
                )
            )

            final_path = session_root / "final" / "final.mp4"
            state.stage = "rendering"
            state.progress_percent = 70
            state.render_report = self.renderer.render(
                timeline=state.timeline,
                asset_manifest=state.asset_manifest,
                audio_manifest=state.audio_manifest,
                output_path=final_path,
            )
            state.stage = "quality_check"
            state.progress_percent = 90
            state.render_validation_report = self.validator.validate(state.render_report.output_path)
            if state.render_validation_report.status != "valid":
                raise RuntimeError("; ".join(state.render_validation_report.issues) or "render validation failed")
            state.stage = "quality_report"
            state.progress_percent = 95
            state.quality_report = self._build_quality_report(state)

            state.status = "completed"
            state.stage = "completed"
            state.progress_percent = 100
            state.artifacts = [
                WorkspaceFileRef(
                    name="final.mp4",
                    path=state.render_report.output_path,
                    description="P0a placeholder short-video render.",
                    source=self.capability,
                )
            ]
            state.production_events.append(
                ProductionEvent(
                    event_type="production_completed",
                    stage=state.stage,
                    message="Placeholder short-video render completed.",
                    metadata={"artifact_path": state.render_report.output_path},
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_to_adk_state(adk_state, state)
            return self._result_from_state(
                state,
                message="P0a placeholder short-video production completed.",
            )
        except Exception as exc:
            state.status = "failed"
            state.stage = "failed"
            state.production_events.append(
                ProductionEvent(
                    event_type="production_failed",
                    stage=state.stage,
                    message=f"Short-video production failed: {type(exc).__name__}: {exc}",
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            return self._result_from_state(
                state,
                message="Short-video production failed.",
                error=ProductionErrorInfo(
                    code="short_video_start_failed",
                    message=f"{type(exc).__name__}: {exc}",
                ),
            )

    async def status(
        self,
        *,
        production_session_id: str | None,
        adk_state,
    ) -> ProductionRunResult:
        """Return a read-only status snapshot for a short-video production run."""
        context = _context_from_adk_state(adk_state)
        session_id = _resolve_requested_session_id(production_session_id, adk_state)
        try:
            state = self.store.load_state(
                production_session_id=session_id,
                adk_session_id=context["sid"],
                owner_ref=context["owner_ref"],
                state_type=ShortVideoProductionState,
                capability=self.capability,
            )
        except ProductionSessionNotFoundError:
            return ProductionRunResult(
                status="failed",
                capability=self.capability,
                production_session_id=session_id or "",
                stage="not_found",
                progress_percent=0,
                message="Production session was not found or is not owned by this conversation.",
                error=ProductionErrorInfo(
                    code="production_session_not_found_or_not_owned",
                    message="Production session was not found or is not owned by this conversation.",
                ),
            )
        return self._result_from_state(state, message=_status_message(state))

    async def view(
        self,
        *,
        production_session_id: str | None,
        view_type: str | None,
        adk_state,
    ) -> ProductionRunResult:
        """Return a read-only production view derived from persisted state."""
        context = _context_from_adk_state(adk_state)
        session_id = _resolve_requested_session_id(production_session_id, adk_state)
        try:
            state = self.store.load_state(
                production_session_id=session_id,
                adk_session_id=context["sid"],
                owner_ref=context["owner_ref"],
                state_type=ShortVideoProductionState,
                capability=self.capability,
            )
        except ProductionSessionNotFoundError:
            return ProductionRunResult(
                status="failed",
                capability=self.capability,
                production_session_id=session_id or "",
                stage="not_found",
                progress_percent=0,
                message="Production session was not found or is not owned by this conversation.",
                error=ProductionErrorInfo(
                    code="production_session_not_found_or_not_owned",
                    message="Production session was not found or is not owned by this conversation.",
                ),
            )

        normalized_view_type = _normalize_view_type(view_type)
        if normalized_view_type is None:
            return ProductionRunResult(
                status="failed",
                capability=self.capability,
                production_session_id=state.production_session.production_session_id,
                stage="invalid_view_type",
                progress_percent=state.progress_percent,
                message=f"Unsupported production view_type. Allowed: {', '.join(_VIEW_TYPES)}.",
                error=ProductionErrorInfo(
                    code="invalid_view_type",
                    message=f"Unsupported production view_type. Allowed: {', '.join(_VIEW_TYPES)}.",
                ),
            )
        return self._result_from_state(
            state,
            message=f"Loaded short-video production view: {normalized_view_type}.",
            view=_build_production_view(state, normalized_view_type),
        )

    async def analyze_revision_impact(
        self,
        *,
        production_session_id: str | None,
        user_response: Any | None,
        adk_state,
    ) -> ProductionRunResult:
        """Return a read-only impact analysis for a requested production revision."""
        context = _context_from_adk_state(adk_state)
        session_id = _resolve_requested_session_id(production_session_id, adk_state)
        try:
            state = self.store.load_state(
                production_session_id=session_id,
                adk_session_id=context["sid"],
                owner_ref=context["owner_ref"],
                state_type=ShortVideoProductionState,
                capability=self.capability,
            )
        except ProductionSessionNotFoundError:
            return ProductionRunResult(
                status="failed",
                capability=self.capability,
                production_session_id=session_id or "",
                stage="not_found",
                progress_percent=0,
                message="Production session was not found or is not owned by this conversation.",
                error=ProductionErrorInfo(
                    code="production_session_not_found_or_not_owned",
                    message="Production session was not found or is not owned by this conversation.",
                ),
            )

        return self._result_from_state(
            state,
            message="Loaded short-video revision impact analysis. No production state was changed.",
            view=build_revision_impact_view(state, normalize_user_response(user_response)),
        )

    async def apply_revision(
        self,
        *,
        production_session_id: str | None,
        user_response: Any | None,
        adk_state,
    ) -> ProductionRunResult:
        """Apply a confirmed revision and pause for review before regeneration."""
        context = _context_from_adk_state(adk_state)
        session_id = _resolve_requested_session_id(production_session_id, adk_state)
        try:
            state = self.store.load_state(
                production_session_id=session_id,
                adk_session_id=context["sid"],
                owner_ref=context["owner_ref"],
                state_type=ShortVideoProductionState,
                capability=self.capability,
            )
        except ProductionSessionNotFoundError:
            return ProductionRunResult(
                status="failed",
                capability=self.capability,
                production_session_id=session_id or "",
                stage="not_found",
                progress_percent=0,
                message="Production session was not found or is not owned by this conversation.",
                error=ProductionErrorInfo(
                    code="production_session_not_found_or_not_owned",
                    message="Production session was not found or is not owned by this conversation.",
                ),
            )

        response = normalize_user_response(user_response)
        revision_notes = _revision_notes_from_response(response)
        impact_view = build_revision_impact_view(state, response)
        if not revision_notes:
            return self._result_from_state(
                state,
                message="Please provide concrete revision notes before applying a production change.",
                view=impact_view,
                error=ProductionErrorInfo(
                    code="invalid_revision_request",
                    message="Revision notes are required before applying a production change.",
                ),
            )
        if impact_view.get("unmatched_targets") and not impact_view.get("matched_targets"):
            return self._result_from_state(
                state,
                message="Revision target was not found. Choose one available target before applying the change.",
                view=impact_view,
                error=ProductionErrorInfo(
                    code="revision_target_unmatched",
                    message="Revision target was not found.",
                ),
            )
        target_kinds = _revision_target_kinds(impact_view)
        if state.asset_plan is None and state.storyboard is not None:
            state.brief_summary = _append_revision_note(state.brief_summary, revision_notes)
            state.storyboard = _build_short_video_storyboard(
                user_prompt=state.brief_summary,
                reference_assets=_valid_reference_assets(state),
                selected_ratio=state.storyboard.selected_ratio or state.planning_context.get("selected_ratio"),
                duration_seconds=_planning_duration_seconds(state),
                video_type=_planning_video_type(state),
            )
            state.status = "needs_user_review"
            state.stage = "storyboard_review"
            state.progress_percent = 15
            state.active_breakpoint = ProductionBreakpoint(
                stage=state.stage,
                review_payload=_storyboard_review_payload(state),
            )
            state.production_events.append(
                ProductionEvent(
                    event_type="storyboard_revision_applied",
                    stage=state.stage,
                    message="Applied user revision to the short-video storyboard and paused for review.",
                    metadata={
                        "user_response": response,
                        "target_kinds": sorted(target_kinds),
                        "impact_level": impact_view.get("impact_level", ""),
                    },
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_pointer_to_adk_state(adk_state, state)
            return self._result_from_state(
                state,
                message="Revision was applied to the storyboard. Please review it before asset planning.",
                view=build_revision_impact_view(state, response),
            )
        if state.asset_plan is None:
            return self._result_from_state(
                state,
                message="No asset plan exists for this production session.",
                view=impact_view,
                error=ProductionErrorInfo(
                    code="invalid_state",
                    message="No asset plan exists for this production session.",
                ),
            )
        state.brief_summary = _append_revision_note(state.brief_summary, revision_notes)
        _apply_revision_to_asset_plan(
            state,
            notes=revision_notes,
            target_kinds=target_kinds,
            matched_targets=impact_view.get("matched_targets", []),
        )
        _mark_revision_outputs_stale(
            state,
            impacted=impact_view.get("impacted", []),
            reason=f"Revision applied: {revision_notes}",
        )
        state.status = "needs_user_review"
        state.stage = "asset_plan_review"
        state.progress_percent = 30
        state.active_breakpoint = ProductionBreakpoint(
            stage=state.stage,
            review_payload=_asset_plan_review_payload(state),
        )
        state.production_events.append(
            ProductionEvent(
                event_type="revision_applied",
                stage=state.stage,
                message="Applied user revision to the short-video plan and paused for review.",
                metadata={
                    "user_response": response,
                    "target_kinds": sorted(target_kinds),
                    "impact_level": impact_view.get("impact_level", ""),
                    "impacted": [
                        {
                            "kind": item.get("kind", ""),
                            "id": item.get("id", ""),
                        }
                        for item in impact_view.get("impacted", [])
                        if isinstance(item, dict)
                    ],
                },
            )
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_pointer_to_adk_state(adk_state, state)
        return self._result_from_state(
            state,
            message="Revision was applied to the plan. Please review the updated asset plan before regeneration.",
            view=build_revision_impact_view(state, response),
        )

    async def add_reference_assets(
        self,
        *,
        production_session_id: str | None,
        input_files: list[Any],
        user_response: Any | None,
        adk_state,
    ) -> ProductionRunResult:
        """Add or replace user reference assets in an existing production session."""
        context = _context_from_adk_state(adk_state)
        session_id = _resolve_requested_session_id(production_session_id, adk_state)
        try:
            state = self.store.load_state(
                production_session_id=session_id,
                adk_session_id=context["sid"],
                owner_ref=context["owner_ref"],
                state_type=ShortVideoProductionState,
                capability=self.capability,
            )
        except ProductionSessionNotFoundError:
            return ProductionRunResult(
                status="failed",
                capability=self.capability,
                production_session_id=session_id or "",
                stage="not_found",
                progress_percent=0,
                message="Production session was not found or is not owned by this conversation.",
                error=ProductionErrorInfo(
                    code="production_session_not_found_or_not_owned",
                    message="Production session was not found or is not owned by this conversation.",
                ),
            )

        new_references = _reference_assets_from_input_files(
            input_files=input_files,
            turn_index=context["turn_index"],
        )
        if not new_references:
            return self._result_from_state(
                state,
                message="No valid reference assets were provided.",
                error=ProductionErrorInfo(
                    code="invalid_input",
                    message="No valid reference assets were provided.",
                ),
            )

        response = normalize_user_response(user_response)
        replace_reference_id = _replacement_reference_id(response)
        replaced_reference_ids: list[str] = []
        if replace_reference_id:
            replaced_reference_ids = _mark_reference_replaced(
                state,
                replace_reference_id=replace_reference_id,
                replacement_reference_id=new_references[0].reference_asset_id,
            )
            if not replaced_reference_ids:
                return self._result_from_state(
                    state,
                    message=f"Reference asset to replace was not found: {replace_reference_id}.",
                    error=ProductionErrorInfo(
                        code="reference_asset_not_found",
                        message=f"Reference asset to replace was not found: {replace_reference_id}.",
                    ),
                )

        state.reference_assets.extend(new_references)
        reason = "Reference assets changed; dependent production outputs need review."
        _mark_generated_outputs_stale(
            state,
            reason=reason,
            artifact_notice="May be stale after reference asset changes.",
        )
        selected_ratio = (
            state.asset_plan.selected_ratio
            if state.asset_plan is not None
            else (state.storyboard.selected_ratio if state.storyboard is not None else state.planning_context.get("selected_ratio"))
        )
        duration_seconds = state.asset_plan.duration_seconds if state.asset_plan is not None else _planning_duration_seconds(state)
        current_provider, current_model_name, current_resolution = _asset_plan_provider_settings(state.asset_plan)
        video_type = (
            state.asset_plan.video_type
            if state.asset_plan is not None
            else _planning_video_type(state)
        )
        state.planning_context.update(
            {
                "selected_ratio": selected_ratio,
                "duration_seconds": duration_seconds,
                "video_type": video_type,
                "video_provider": current_provider,
                "video_model_name": current_model_name,
                "video_resolution": current_resolution,
            }
        )
        state.storyboard = _build_short_video_storyboard(
            user_prompt=state.brief_summary,
            reference_assets=_valid_reference_assets(state),
            selected_ratio=selected_ratio,
            duration_seconds=duration_seconds,
            video_type=video_type,
        )
        state.asset_plan = None
        state.status = "needs_user_review"
        state.stage = "storyboard_review"
        state.progress_percent = max(state.progress_percent, 20)
        state.active_breakpoint = ProductionBreakpoint(
            stage=state.stage,
            review_payload=_storyboard_review_payload(state),
        )
        state.production_events.append(
            ProductionEvent(
                event_type="reference_assets_added",
                stage=state.stage,
                message="Added reference assets and returned to storyboard review.",
                metadata={
                    "added_reference_asset_ids": [
                        item.reference_asset_id for item in new_references
                    ],
                    "replaced_reference_asset_ids": replaced_reference_ids,
                    "impacted": [
                        "asset_plan",
                        "asset_manifest",
                        "audio_manifest",
                        "timeline",
                        "artifacts",
                    ],
                },
            )
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_pointer_to_adk_state(adk_state, state)
        return self._result_from_state(
            state,
            message=(
                f"Added {len(new_references)} reference asset(s). "
                "The storyboard is ready for review again."
            ),
        )

    async def resume(
        self,
        *,
        production_session_id: str | None,
        user_response: Any | None,
        adk_state,
    ) -> ProductionRunResult:
        """Resume a short-video production session from an active review breakpoint."""
        context = _context_from_adk_state(adk_state)
        session_id = _resolve_requested_session_id(production_session_id, adk_state)
        try:
            state = self.store.load_state(
                production_session_id=session_id,
                adk_session_id=context["sid"],
                owner_ref=context["owner_ref"],
                state_type=ShortVideoProductionState,
                capability=self.capability,
            )
        except ProductionRuntimeError:
            return await self.status(production_session_id=session_id, adk_state=adk_state)
        response = normalize_user_response(user_response)
        decision = _normalize_resume_decision(response)
        if state.active_breakpoint is None:
            state.production_events.append(
                ProductionEvent(
                    event_type="resume_ignored",
                    stage=state.stage,
                    message="Resume ignored because there is no active production breakpoint.",
                    metadata={"user_response": response},
                )
            )
            self.store.save_state(state)
            return self._result_from_state(
                state,
                message="There is no active review breakpoint to resume.",
            )

        if decision == "cancel":
            state.status = "cancelled"
            state.stage = "cancelled"
            state.active_breakpoint = None
            state.production_events.append(
                ProductionEvent(
                    event_type="production_cancelled",
                    stage=state.stage,
                    message="User cancelled short-video production.",
                    metadata={"user_response": response},
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_to_adk_state(adk_state, state)
            return self._result_from_state(state, message="Short-video production was cancelled.")

        if decision == "revise":
            if state.active_breakpoint.stage == "storyboard_review":
                return self._revise_storyboard_and_pause(
                    state,
                    user_response=response,
                    adk_state=adk_state,
                )
            if state.active_breakpoint.stage == "shot_review":
                return self._revise_shot_segment_and_return_to_asset_plan_review(
                    state,
                    user_response=response,
                    adk_state=adk_state,
                )
            return self._revise_asset_plan_and_pause(
                state,
                user_response=response,
                adk_state=adk_state,
            )

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
            self.store.project_to_adk_state(adk_state, state)
            return self._result_from_state(
                state,
                message="Please respond with decision=approve, revise, or cancel.",
            )

        if state.active_breakpoint.stage == "storyboard_review":
            return self._approve_storyboard_and_prepare_asset_plan_review(
                state,
                user_response=response,
                adk_state=adk_state,
            )
        if state.active_breakpoint.stage == "asset_plan_review":
            return await self._approve_asset_plan_and_generate(
                state,
                user_response=response,
                adk_state=adk_state,
            )
        if state.active_breakpoint.stage == "shot_review":
            return await self._approve_shot_segment_and_continue(
                state,
                user_response=response,
                adk_state=adk_state,
            )

        state.production_events.append(
            ProductionEvent(
                event_type="resume_stage_unsupported",
                stage=state.stage,
                message="Resume decision was not valid for the current production breakpoint.",
                metadata={"user_response": response},
            )
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_to_adk_state(adk_state, state)
        return self._result_from_state(
            state,
            message=f"Current review stage cannot be approved by this action: {state.active_breakpoint.stage}.",
        )

    def _prepare_storyboard_review(
        self,
        state: ShortVideoProductionState,
        *,
        user_prompt: str,
        render_settings_payload: dict[str, Any],
        adk_state,
    ) -> ProductionRunResult:
        """Create a P1a storyboard and pause before asset-plan generation."""
        duration_seconds = _duration_seconds(render_settings_payload, default=8.0)
        video_provider = _requested_short_video_provider(render_settings_payload)
        video_model_name = _requested_video_model_name(render_settings_payload, video_provider)
        video_resolution = _requested_video_resolution(render_settings_payload, video_provider, video_model_name)
        video_type = _requested_video_type(user_prompt, render_settings_payload)
        selected_ratio = _explicit_aspect_ratio(render_settings_payload)
        state.planning_context = {
            "user_prompt": user_prompt,
            "render_settings": dict(render_settings_payload),
            "duration_seconds": duration_seconds,
            "video_type": video_type,
            "video_provider": video_provider,
            "video_model_name": video_model_name,
            "video_resolution": video_resolution,
            "selected_ratio": selected_ratio,
        }
        state.storyboard = _build_short_video_storyboard(
            user_prompt=user_prompt,
            reference_assets=state.reference_assets,
            selected_ratio=selected_ratio,
            duration_seconds=duration_seconds,
            video_type=video_type,
        )
        state.status = "needs_user_review"
        state.stage = "storyboard_review"
        state.progress_percent = 15
        state.active_breakpoint = ProductionBreakpoint(
            stage=state.stage,
            review_payload=_storyboard_review_payload(state),
        )
        state.production_events.append(
            ProductionEvent(
                event_type="storyboard_review_required",
                stage=state.stage,
                message="Prepared a P1a short-video storyboard and paused before asset-plan generation.",
                metadata={
                    "storyboard_id": state.storyboard.storyboard_id,
                    "video_type": state.storyboard.video_type,
                    "selected_ratio": state.storyboard.selected_ratio,
                    "shot_count": len(state.storyboard.shots),
                },
            )
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_to_adk_state(adk_state, state)
        return self._result_from_state(
            state,
            message="Please review the short-video storyboard before provider-specific asset planning.",
        )

    def _approve_storyboard_and_prepare_asset_plan_review(
        self,
        state: ShortVideoProductionState,
        *,
        user_response: dict[str, Any],
        adk_state,
    ) -> ProductionRunResult:
        """Approve the storyboard and create the provider-specific asset plan review."""
        if state.storyboard is None:
            return self._fail_state(
                state,
                adk_state=adk_state,
                code="invalid_state",
                message="No storyboard exists for this production session.",
            )

        selected_ratio = _resume_selected_ratio(
            user_response,
            state.storyboard.selected_ratio or state.planning_context.get("selected_ratio"),
        )
        state.storyboard.selected_ratio = selected_ratio  # type: ignore[assignment]
        state.storyboard.status = "approved"
        state.planning_context["selected_ratio"] = selected_ratio
        duration_seconds = _planning_duration_seconds(state)
        video_provider, video_model_name, video_resolution = _planning_provider_settings(state)
        video_type = _planning_video_type(state)
        state.asset_plan = _build_short_video_asset_plan(
            user_prompt=state.brief_summary,
            reference_assets=_valid_reference_assets(state),
            selected_ratio=selected_ratio,
            duration_seconds=duration_seconds,
            video_type=video_type,
            video_provider=video_provider,
            video_model_name=video_model_name,
            video_resolution=video_resolution,
            storyboard=state.storyboard,
        )
        state.shot_asset_plans = _build_shot_asset_plans(
            storyboard=state.storyboard,
            asset_plan=state.asset_plan,
        )
        state.status = "needs_user_review"
        state.stage = "asset_plan_review"
        state.progress_percent = 25
        state.active_breakpoint = ProductionBreakpoint(
            stage=state.stage,
            review_payload=_asset_plan_review_payload(state),
        )
        state.production_events.append(
            ProductionEvent(
                event_type="storyboard_approved",
                stage=state.stage,
                message="User approved the storyboard; prepared asset-plan review before provider generation.",
                metadata={
                    "storyboard_id": state.storyboard.storyboard_id,
                    "asset_plan_id": state.asset_plan.plan_id,
                    "selected_ratio": selected_ratio,
                },
            )
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_to_adk_state(adk_state, state)
        return self._result_from_state(
            state,
            message="Storyboard approved. Please review the short-video asset plan before real generation.",
        )

    def _revise_storyboard_and_pause(
        self,
        state: ShortVideoProductionState,
        *,
        user_response: dict[str, Any],
        adk_state,
    ) -> ProductionRunResult:
        """Apply storyboard revision notes and remain at storyboard review."""
        notes = str(user_response.get("notes", "") or user_response.get("message", "") or "").strip()
        if notes:
            state.brief_summary = f"{state.brief_summary}\n\nStoryboard revision notes: {notes}"
        selected_ratio = _resume_selected_ratio(
            user_response,
            state.storyboard.selected_ratio if state.storyboard is not None else state.planning_context.get("selected_ratio"),
        )
        state.planning_context["selected_ratio"] = selected_ratio
        state.storyboard = _build_short_video_storyboard(
            user_prompt=state.brief_summary,
            reference_assets=_valid_reference_assets(state),
            selected_ratio=selected_ratio,
            duration_seconds=_planning_duration_seconds(state),
            video_type=_planning_video_type(state),
        )
        state.asset_plan = None
        state.status = "needs_user_review"
        state.stage = "storyboard_review"
        state.progress_percent = 15
        state.active_breakpoint = ProductionBreakpoint(
            stage=state.stage,
            review_payload=_storyboard_review_payload(state),
        )
        state.production_events.append(
            ProductionEvent(
                event_type="storyboard_revision_requested",
                stage=state.stage,
                message="User requested storyboard revision.",
                metadata={"user_response": user_response},
            )
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_to_adk_state(adk_state, state)
        return self._result_from_state(
            state,
            message="Storyboard revision notes were recorded. Please approve, revise again, or cancel the updated storyboard.",
        )

    def _revise_asset_plan_and_pause(
        self,
        state: ShortVideoProductionState,
        *,
        user_response: dict[str, Any],
        adk_state,
    ) -> ProductionRunResult:
        """Apply asset-plan revision notes and remain at asset-plan review."""
        notes = str(user_response.get("notes", "") or user_response.get("message", "") or "").strip()
        if notes:
            state.brief_summary = f"{state.brief_summary}\n\nRevision notes: {notes}"
        current_ratio = state.asset_plan.selected_ratio if state.asset_plan is not None else state.planning_context.get("selected_ratio")
        duration_seconds = state.asset_plan.duration_seconds if state.asset_plan is not None else _planning_duration_seconds(state)
        current_provider, current_model_name, current_resolution = _asset_plan_provider_settings(state.asset_plan)
        state.asset_plan = _build_short_video_asset_plan(
            user_prompt=state.brief_summary,
            reference_assets=_valid_reference_assets(state),
            selected_ratio=current_ratio,
            duration_seconds=duration_seconds,
            video_type=(
                state.asset_plan.video_type
                if state.asset_plan is not None
                else _planning_video_type(state)
            ),
            video_provider=current_provider,
            video_model_name=current_model_name,
            video_resolution=current_resolution,
            storyboard=state.storyboard,
        )
        if notes:
            state.asset_plan.shot_plan.voiceover_text = _build_voiceover_text(state.brief_summary)
        state.shot_asset_plans = _build_shot_asset_plans(
            storyboard=state.storyboard,
            asset_plan=state.asset_plan,
        )
        state.active_breakpoint = ProductionBreakpoint(
            stage=state.stage,
            review_payload=_asset_plan_review_payload(state),
        )
        state.production_events.append(
            ProductionEvent(
                event_type="asset_plan_revision_requested",
                stage=state.stage,
                message="User requested asset-plan revision.",
                metadata={"user_response": user_response},
            )
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_to_adk_state(adk_state, state)
        return self._result_from_state(
            state,
            message="Revision notes were recorded. Please approve, revise again, or cancel the updated plan.",
        )

    def _prepare_asset_plan_review(
        self,
        state: ShortVideoProductionState,
        *,
        user_prompt: str,
        render_settings_payload: dict[str, Any],
        adk_state,
    ) -> ProductionRunResult:
        """Create the P0 short-video asset plan and pause before provider calls."""
        duration_seconds = _duration_seconds(render_settings_payload, default=8.0)
        video_provider = _requested_short_video_provider(render_settings_payload)
        video_model_name = _requested_video_model_name(render_settings_payload, video_provider)
        video_resolution = _requested_video_resolution(render_settings_payload, video_provider, video_model_name)
        state.asset_plan = _build_short_video_asset_plan(
            user_prompt=user_prompt,
            reference_assets=state.reference_assets,
            selected_ratio=_explicit_aspect_ratio(render_settings_payload),
            duration_seconds=duration_seconds,
            video_type=_requested_video_type(user_prompt, render_settings_payload),
            video_provider=video_provider,
            video_model_name=video_model_name,
            video_resolution=video_resolution,
        )
        state.shot_asset_plans = _build_shot_asset_plans(
            storyboard=state.storyboard,
            asset_plan=state.asset_plan,
        )
        state.status = "needs_user_review"
        state.stage = "asset_plan_review"
        state.progress_percent = 20
        state.active_breakpoint = ProductionBreakpoint(
            stage=state.stage,
            review_payload=_asset_plan_review_payload(state),
        )
        state.production_events.append(
            ProductionEvent(
                event_type="asset_plan_review_required",
                stage=state.stage,
                message="Prepared a P0 short-video asset plan and paused before provider generation.",
                metadata={
                    "video_type": state.asset_plan.video_type,
                    "planned_video_provider": state.asset_plan.planned_video_provider,
                    "planned_video_model_name": state.asset_plan.planned_video_model_name,
                    "planned_video_resolution": state.asset_plan.planned_video_resolution,
                    "planned_tts_provider": state.asset_plan.planned_tts_provider,
                    "selected_ratio": state.asset_plan.selected_ratio,
                },
            )
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_to_adk_state(adk_state, state)
        return self._result_from_state(
            state,
            message="Please review the short-video asset plan before real video and native audio generation.",
        )

    async def _approve_asset_plan_and_generate(
        self,
        state: ShortVideoProductionState,
        *,
        user_response: dict[str, Any],
        adk_state,
    ) -> ProductionRunResult:
        """Generate the next provider shot segment from an approved asset plan."""
        if state.asset_plan is None:
            return self._fail_state(
                state,
                adk_state=adk_state,
                code="invalid_state",
                message="No asset plan exists for this production session.",
            )

        selected_ratio = _resume_selected_ratio(user_response, state.asset_plan.selected_ratio)
        ratio_options = list(state.asset_plan.ratio_options or ["9:16", "16:9", "1:1"])
        if selected_ratio is None or selected_ratio not in ratio_options:
            state.status = "needs_user_review"
            state.stage = "asset_plan_review"
            state.progress_percent = max(state.progress_percent, 20)
            state.active_breakpoint = ProductionBreakpoint(
                stage=state.stage,
                review_payload=_asset_plan_review_payload(state),
            )
            state.production_events.append(
                ProductionEvent(
                    event_type="ratio_required",
                    stage=state.stage,
                    message="A supported video aspect ratio must be selected before provider generation.",
                    metadata={"user_response": user_response},
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_to_adk_state(adk_state, state)
            return self._result_from_state(
                state,
                message=f"Please choose one supported aspect ratio before approving generation: {', '.join(ratio_options)}.",
            )

        state.asset_plan.selected_ratio = selected_ratio
        state.asset_plan.status = "approved"
        state.active_breakpoint = None
        if _has_partial_shot_regeneration_pending(state):
            _prepare_partial_shot_regeneration(
                state,
                selected_ratio=selected_ratio,
            )
            return await self._generate_next_shot_segment_and_pause(
                state,
                adk_state=adk_state,
                message_prefix="Approved the revised shot segment plan.",
            )

        state.shot_asset_plans = _build_shot_asset_plans(
            storyboard=state.storyboard,
            asset_plan=state.asset_plan,
        )
        _mark_existing_generated_media_superseded(
            state,
            reason="New approved generation supersedes previous generated outputs.",
        )
        _mark_existing_shot_outputs_stale(
            state,
            reason="New approved shot plan supersedes previous shot previews.",
        )
        return await self._generate_next_shot_segment_and_pause(
            state,
            adk_state=adk_state,
            message_prefix="Approved the asset plan.",
        )

    async def _approve_shot_segment_and_continue(
        self,
        state: ShortVideoProductionState,
        *,
        user_response: dict[str, Any],
        adk_state,
    ) -> ProductionRunResult:
        """Approve the current generated shot segment and continue or finalize."""
        artifact = _current_review_shot_artifact(state)
        if artifact is None:
            return self._fail_state(
                state,
                adk_state=adk_state,
                code="invalid_state",
                message="No generated shot segment is waiting for review.",
            )

        artifact.status = "approved"
        segment_plan = _shot_asset_plan_by_id(state, artifact.shot_asset_plan_id)
        if segment_plan is not None:
            segment_plan.status = "reviewed"
        state.production_events.append(
            ProductionEvent(
                event_type="shot_segment_approved",
                stage=state.stage,
                message="User approved the generated shot segment.",
                metadata={
                    "shot_artifact_id": artifact.shot_artifact_id,
                    "shot_asset_plan_id": artifact.shot_asset_plan_id,
                    "user_response": user_response,
                },
            )
        )

        if _next_pending_shot_asset_plan(state) is not None:
            return await self._generate_next_shot_segment_and_pause(
                state,
                adk_state=adk_state,
                message_prefix="Approved the previous shot segment.",
            )
        return self._finalize_approved_shot_segments(state, adk_state=adk_state)

    def _revise_shot_segment_and_return_to_asset_plan_review(
        self,
        state: ShortVideoProductionState,
        *,
        user_response: dict[str, Any],
        adk_state,
    ) -> ProductionRunResult:
        """Record shot review notes and return to gated asset-plan review."""
        notes = _revision_notes_from_response(user_response)
        if notes:
            state.brief_summary = _append_revision_note(state.brief_summary, notes)

        artifact = _current_review_shot_artifact(state)
        if artifact is not None:
            artifact.status = "stale"
            segment_plan = _shot_asset_plan_by_id(state, artifact.shot_asset_plan_id)
            if segment_plan is not None:
                _apply_notes_to_shot_segment_plan(
                    segment_plan,
                    notes=notes,
                    target_kinds={"shot_asset_plan"},
                )
            _mark_shot_artifact_media_stale(
                state,
                artifact,
                reason="Shot review revision requested before continuing generation.",
            )
        if state.asset_plan is not None:
            state.asset_plan.status = "draft"
        state.artifacts = []
        state.status = "needs_user_review"
        state.stage = "asset_plan_review"
        state.progress_percent = max(state.progress_percent, 25)
        state.active_breakpoint = ProductionBreakpoint(
            stage=state.stage,
            review_payload=_asset_plan_review_payload(state),
        )
        state.production_events.append(
            ProductionEvent(
                event_type="shot_segment_revision_requested",
                stage=state.stage,
                message="User requested changes after reviewing a generated shot segment.",
                metadata={"user_response": user_response},
            )
        )
        adk_state["final_file_paths"] = []
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_to_adk_state(adk_state, state)
        return self._result_from_state(
            state,
            message="Shot revision notes were recorded. Please review the updated plan before regenerating.",
        )

    async def _generate_next_shot_segment_and_pause(
        self,
        state: ShortVideoProductionState,
        *,
        adk_state,
        message_prefix: str,
    ) -> ProductionRunResult:
        """Generate one pending shot segment and pause for user review."""
        if state.asset_plan is None:
            return self._fail_state(
                state,
                adk_state=adk_state,
                code="invalid_state",
                message="No approved asset plan exists for shot generation.",
            )
        segment_plan = _next_pending_shot_asset_plan(state)
        if segment_plan is None:
            return self._finalize_approved_shot_segments(state, adk_state=adk_state)

        session_root = self.store.session_root(state.production_session)
        settings = _normalize_render_settings({"aspect_ratio": segment_plan.selected_ratio or "16:9"})
        segment_asset_plan = _asset_plan_for_shot_segment(state.asset_plan, segment_plan)
        try:
            segment_plan.status = "generating"
            state.status = "running"
            state.stage = "provider_generation"
            state.progress_percent = _segment_progress(segment_plan, state.shot_asset_plans, base=35)
            _publish_short_video_progress(
                adk_state,
                state,
                title="Generating Shot Segment",
                detail=(
                    f"Generating segment {segment_plan.segment_index} of {len(state.shot_asset_plans)} "
                    f"({state.progress_percent}%)."
                ),
            )
            video_asset = await self.provider_runtime.generate_video_clip(
                session_root=session_root,
                asset_plan=segment_asset_plan,
                render_settings=settings,
                reference_assets=state.reference_assets,
                owner_ref=state.production_session.owner_ref,
            )
            state.asset_manifest.append(video_asset)

            state.stage = "audio_generation"
            state.progress_percent = _segment_progress(segment_plan, state.shot_asset_plans, base=50)
            _publish_short_video_progress(
                adk_state,
                state,
                title="Preparing Segment Audio",
                detail=(
                    f"Preparing audio for segment {segment_plan.segment_index} "
                    f"({state.progress_percent}%)."
                ),
            )
            audio_asset = await self.provider_runtime.synthesize_voiceover(
                session_root=session_root,
                asset_plan=segment_asset_plan,
                render_settings=settings,
                owner_ref=state.production_session.owner_ref,
            )
            state.audio_manifest.append(audio_asset)

            preview_timeline = _build_single_clip_timeline(
                asset_id=video_asset.asset_id,
                audio_id=audio_asset.audio_id,
                audio_kind="voiceover",
                render_settings=settings,
                duration_seconds=segment_plan.duration_seconds,
            )
            preview_path = _shot_preview_output_path(session_root, segment_plan.shot_asset_plan_id)
            state.stage = "shot_preview_rendering"
            state.progress_percent = _segment_progress(segment_plan, state.shot_asset_plans, base=60)
            preview_report = self.renderer.render(
                timeline=preview_timeline,
                asset_manifest=state.asset_manifest,
                audio_manifest=state.audio_manifest,
                output_path=preview_path,
            )
            preview_validation = self.validator.validate(preview_report.output_path)
            if preview_validation.status != "valid":
                raise RuntimeError("; ".join(preview_validation.issues) or "shot preview validation failed")

            segment_plan.status = "generated"
            shot_artifact = ShortVideoShotArtifact(
                shot_asset_plan_id=segment_plan.shot_asset_plan_id,
                segment_index=segment_plan.segment_index,
                storyboard_shot_ids=segment_plan.storyboard_shot_ids,
                video_asset_id=video_asset.asset_id,
                audio_id=audio_asset.audio_id,
                preview_path=preview_report.output_path,
                metadata={
                    "storyboard_sequence_indexes": segment_plan.storyboard_sequence_indexes,
                    "render_report": preview_report.model_dump(mode="json"),
                    "validation_report": preview_validation.model_dump(mode="json"),
                },
            )
            state.shot_artifacts.append(shot_artifact)
            state.timeline = preview_timeline
            state.status = "needs_user_review"
            state.stage = "shot_review"
            state.progress_percent = _segment_progress(segment_plan, state.shot_asset_plans, base=70)
            state.artifacts = [
                WorkspaceFileRef(
                    name=f"shot_segment_{segment_plan.segment_index}_preview.mp4",
                    path=preview_report.output_path,
                    description=(
                        "Shot segment preview. Approve it to continue generation; "
                        "it is not the final deliverable yet."
                    ),
                    source=self.capability,
                )
            ]
            state.active_breakpoint = ProductionBreakpoint(
                stage=state.stage,
                review_payload=_shot_review_payload(state, shot_artifact),
            )
            state.production_events.append(
                ProductionEvent(
                    event_type="shot_review_required",
                    stage=state.stage,
                    message="Generated one shot segment preview and paused for user review.",
                    metadata={
                        "shot_artifact_id": shot_artifact.shot_artifact_id,
                        "shot_asset_plan_id": segment_plan.shot_asset_plan_id,
                        "preview_path": shot_artifact.preview_path,
                    },
                )
            )
            _publish_short_video_progress(
                adk_state,
                state,
                title="Shot Segment Ready",
                detail=(
                    f"Segment {segment_plan.segment_index} is ready for review "
                    f"({state.progress_percent}%)."
                ),
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_to_adk_state(adk_state, state)
            return self._result_from_state(
                state,
                message=(
                    f"{message_prefix} Generated shot segment {segment_plan.segment_index} "
                    "and paused for review before continuing."
                ),
            )
        except ShortVideoProviderError as exc:
            segment_plan.status = "failed"
            return self._fail_state(
                state,
                adk_state=adk_state,
                code="provider_failed",
                message=str(exc),
                provider=state.asset_plan.planned_video_provider,
            )
        except Exception as exc:
            segment_plan.status = "failed"
            return self._fail_state(
                state,
                adk_state=adk_state,
                code="short_video_p1b_segment_failed",
                message=f"{type(exc).__name__}: {exc}",
            )

    def _finalize_approved_shot_segments(
        self,
        state: ShortVideoProductionState,
        *,
        adk_state,
    ) -> ProductionRunResult:
        """Render a final video from all approved shot segment previews."""
        if state.asset_plan is None:
            return self._fail_state(
                state,
                adk_state=adk_state,
                code="invalid_state",
                message="No approved asset plan exists for final rendering.",
            )
        approved_artifacts = _approved_shot_artifacts_in_plan_order(state)
        if not approved_artifacts:
            return self._fail_state(
                state,
                adk_state=adk_state,
                code="invalid_state",
                message="No approved shot segments are available for final rendering.",
            )

        session_root = self.store.session_root(state.production_session)
        settings = _normalize_render_settings({"aspect_ratio": state.asset_plan.selected_ratio or "16:9"})
        _ensure_preview_assets_for_final_concat(
            state,
            approved_artifacts=approved_artifacts,
            render_settings=settings,
        )
        state.timeline = _build_multi_clip_timeline(
            shot_artifacts=approved_artifacts,
            shot_asset_plans=state.shot_asset_plans,
            render_settings=settings,
        )
        final_path = _final_output_path(session_root, state.asset_plan.plan_id)
        state.status = "running"
        state.stage = "rendering"
        state.progress_percent = 88
        _publish_short_video_progress(
            adk_state,
            state,
            title="Rendering Final Short Video",
            detail=f"Rendering the approved shot segments into the final MP4 ({state.progress_percent}%).",
        )
        try:
            state.render_report = self.renderer.render(
                timeline=state.timeline,
                asset_manifest=state.asset_manifest,
                audio_manifest=state.audio_manifest,
                output_path=final_path,
            )
            state.stage = "validation"
            state.progress_percent = 95
            state.render_validation_report = self.validator.validate(state.render_report.output_path)
            if state.render_validation_report.status != "valid":
                raise RuntimeError("; ".join(state.render_validation_report.issues) or "render validation failed")
            state.stage = "quality_report"
            state.progress_percent = 98
            state.quality_report = self._build_quality_report(state)
        except Exception as exc:
            return self._fail_state(
                state,
                adk_state=adk_state,
                code="short_video_p1b_final_render_failed",
                message=f"{type(exc).__name__}: {exc}",
            )

        state.status = "completed"
        state.stage = "completed"
        state.progress_percent = 100
        state.active_breakpoint = None
        state.artifacts = [
            WorkspaceFileRef(
                name="final.mp4",
                path=state.render_report.output_path,
                description=f"P1c {_video_type_label(state.asset_plan.video_type)} short-video render.",
                source=self.capability,
            )
        ]
        state.production_events.append(
            ProductionEvent(
                event_type="production_completed",
                stage=state.stage,
                message=f"P1c {_video_type_label(state.asset_plan.video_type)} short-video render completed.",
                metadata={
                    "artifact_path": state.render_report.output_path,
                    "shot_segments": len(approved_artifacts),
                    "quality_report_status": (
                        state.quality_report.status if state.quality_report is not None else ""
                    ),
                },
            )
        )
        _publish_short_video_progress(
            adk_state,
            state,
            title="Short Video Completed",
            detail=f"Final MP4 is ready ({state.progress_percent}%).",
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_to_adk_state(adk_state, state)
        return self._result_from_state(
            state,
            message=f"P1c {_video_type_label(state.asset_plan.video_type)} short-video production completed.",
        )

    def _build_quality_report(self, state: ShortVideoProductionState) -> ShortVideoQualityReport:
        """Build and attach a deterministic quality report for a rendered output."""
        report_path = f"{state.production_session.root_dir}/quality_report.json"
        report = build_quality_report(state, report_path=report_path)
        state.production_events.append(
            ProductionEvent(
                event_type="quality_report_created",
                stage=state.stage,
                message=f"Short-video quality report created with status: {report.status}.",
                metadata={
                    "quality_report_status": report.status,
                    "quality_report_path": report_path,
                    "recommendations": report.recommendations,
                },
            )
        )
        return report

    def _fail_state(
        self,
        state: ShortVideoProductionState,
        *,
        adk_state,
        code: str,
        message: str,
        provider: str = "",
    ) -> ProductionRunResult:
        """Persist and return a structured production failure."""
        failed_stage = state.stage
        state.status = "failed"
        state.stage = "failed"
        state.production_events.append(
            ProductionEvent(
                event_type="production_failed",
                stage=failed_stage,
                message=message,
                metadata={"code": code, "provider": provider},
            )
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_to_adk_state(adk_state, state)
        return self._result_from_state(
            state,
            message="Short-video production failed.",
            error=ProductionErrorInfo(
                code=code,
                message=message,
                details={"provider": provider} if provider else {},
            ),
        )

    def _result_from_state(
        self,
        state: ShortVideoProductionState,
        *,
        message: str,
        view: dict[str, Any] | None = None,
        error: ProductionErrorInfo | None = None,
    ) -> ProductionRunResult:
        state_ref = f"{state.production_session.root_dir}/state.json"
        return ProductionRunResult(
            status=state.status,
            capability=self.capability,
            production_session_id=state.production_session.production_session_id,
            stage=state.stage,
            progress_percent=state.progress_percent,
            message=message,
            state_ref=state_ref,
            artifacts=state.artifacts,
            review_payload=(
                state.active_breakpoint.review_payload
                if state.active_breakpoint is not None
                else None
            ),
            view=view or {},
            error=error,
            events=state.production_events[-5:],
        )

    def _save_projection_files(self, state: ShortVideoProductionState) -> None:
        """Write human-readable projection files derived from ProductionState."""
        root = self.store.session_root(state.production_session)
        (root / "brief.md").write_text(
            f"# Short Video Brief\n\n{state.brief_summary}\n",
            encoding="utf-8",
        )
        (root / "storyboard.json").write_text(
            json.dumps(
                {
                    "storyboard": (
                        state.storyboard.model_dump(mode="json")
                        if state.storyboard is not None
                        else None
                    ),
                    "active_review": (
                        state.active_breakpoint.review_payload.model_dump(mode="json")
                        if state.active_breakpoint is not None
                        else None
                    ),
                    "reference_assets": [item.model_dump(mode="json") for item in state.reference_assets],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (root / "storyboard.md").write_text(
            _storyboard_markdown(state),
            encoding="utf-8",
        )
        (root / "asset_plan.json").write_text(
            json.dumps(
                {
                    "asset_plan": (
                        state.asset_plan.model_dump(mode="json")
                        if state.asset_plan is not None
                        else None
                    ),
                    "active_review": (
                        state.active_breakpoint.review_payload.model_dump(mode="json")
                        if state.active_breakpoint is not None
                        else None
                    ),
                    "reference_assets": [item.model_dump(mode="json") for item in state.reference_assets],
                    "shot_asset_plans": [item.model_dump(mode="json") for item in state.shot_asset_plans],
                    "shot_artifacts": [item.model_dump(mode="json") for item in state.shot_artifacts],
                    "asset_manifest": [item.model_dump(mode="json") for item in state.asset_manifest],
                    "audio_manifest": [item.model_dump(mode="json") for item in state.audio_manifest],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if state.timeline is not None:
            (root / "timeline.json").write_text(
                state.timeline.model_dump_json(indent=2),
                encoding="utf-8",
            )
        if state.quality_report is not None:
            (root / "quality_report.json").write_text(
                state.quality_report.model_dump_json(indent=2),
                encoding="utf-8",
            )
        (root / "quality_report.md").write_text(
            quality_report_markdown(state.quality_report),
            encoding="utf-8",
        )


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


def _publish_short_video_progress(
    adk_state,
    state: ShortVideoProductionState,
    *,
    title: str,
    detail: str,
) -> None:
    """Publish one best-effort realtime production progress event."""
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
    """Return a positive turn index when one is available."""
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _resolve_requested_session_id(production_session_id: str | None, adk_state) -> str:
    requested = str(production_session_id or "").strip()
    if requested:
        return requested
    return get_active_production_session_id(adk_state, capability="short_video")


def _normalize_view_type(view_type: str | None) -> str | None:
    value = str(view_type or "overview").strip().lower() or "overview"
    return value if value in _VIEW_TYPES else None


def _build_production_view(state: ShortVideoProductionState, view_type: str) -> dict[str, Any]:
    if view_type == "overview":
        return _overview_view(state)
    if view_type == "brief":
        return _brief_view(state)
    if view_type == "storyboard":
        return _storyboard_view(state)
    if view_type == "asset_plan":
        return _asset_plan_view(state)
    if view_type == "timeline":
        return _timeline_view(state)
    if view_type == "quality":
        return _quality_view(state)
    if view_type == "events":
        return _events_view(state)
    if view_type == "artifacts":
        return _artifacts_view(state)
    raise ValueError(f"Unsupported view_type: {view_type}")


def _base_view(state: ShortVideoProductionState, view_type: str) -> dict[str, Any]:
    session = state.production_session
    return {
        "view_type": view_type,
        "production_session_id": session.production_session_id,
        "capability": session.capability,
        "status": state.status,
        "stage": state.stage,
        "progress_percent": state.progress_percent,
        "state_ref": f"{session.root_dir}/state.json",
        "project_root": session.root_dir,
    }


def _overview_view(state: ShortVideoProductionState) -> dict[str, Any]:
    view = _base_view(state, "overview")
    view.update(
        {
            "brief_summary": state.brief_summary,
            "active_review": (
                state.active_breakpoint.review_payload.model_dump(mode="json")
                if state.active_breakpoint is not None
                else None
            ),
            "counts": {
                "reference_assets": len(state.reference_assets),
                "storyboard_shots": len(state.storyboard.shots) if state.storyboard is not None else 0,
                "shot_asset_plans": len(state.shot_asset_plans),
                "shot_artifacts": len(state.shot_artifacts),
                "asset_manifest": len(state.asset_manifest),
                "audio_manifest": len(state.audio_manifest),
                "artifacts": len(state.artifacts),
                "events": len(state.production_events),
            },
            "has_storyboard": state.storyboard is not None,
            "has_timeline": state.timeline is not None,
            "has_render_report": state.render_report is not None,
            "has_quality_report": state.quality_report is not None,
            "artifacts": [item.model_dump(mode="json") for item in state.artifacts],
        }
    )
    return view


def _brief_view(state: ShortVideoProductionState) -> dict[str, Any]:
    view = _base_view(state, "brief")
    view.update(
        {
            "brief_summary": state.brief_summary,
            "brief_path": f"{state.production_session.root_dir}/brief.md",
        }
    )
    return view


def _storyboard_view(state: ShortVideoProductionState) -> dict[str, Any]:
    view = _base_view(state, "storyboard")
    view.update(
        {
            "storyboard": (
                state.storyboard.model_dump(mode="json")
                if state.storyboard is not None
                else None
            ),
            "active_review": (
                state.active_breakpoint.review_payload.model_dump(mode="json")
                if state.active_breakpoint is not None
                else None
            ),
            "storyboard_path": f"{state.production_session.root_dir}/storyboard.json",
            "storyboard_markdown_path": f"{state.production_session.root_dir}/storyboard.md",
        }
    )
    return view


def _asset_plan_view(state: ShortVideoProductionState) -> dict[str, Any]:
    view = _base_view(state, "asset_plan")
    view.update(
        {
            "asset_plan": (
                state.asset_plan.model_dump(mode="json")
                if state.asset_plan is not None
                else None
            ),
            "shot_asset_plans": [item.model_dump(mode="json") for item in state.shot_asset_plans],
            "shot_artifacts": [item.model_dump(mode="json") for item in state.shot_artifacts],
            "active_review": (
                state.active_breakpoint.review_payload.model_dump(mode="json")
                if state.active_breakpoint is not None
                else None
            ),
            "reference_assets": [item.model_dump(mode="json") for item in state.reference_assets],
            "asset_plan_path": f"{state.production_session.root_dir}/asset_plan.json",
        }
    )
    return view


def _timeline_view(state: ShortVideoProductionState) -> dict[str, Any]:
    view = _base_view(state, "timeline")
    view.update(
        {
            "timeline": (
                state.timeline.model_dump(mode="json")
                if state.timeline is not None
                else None
            ),
            "render_report": (
                state.render_report.model_dump(mode="json")
                if state.render_report is not None
                else None
            ),
            "render_validation_report": (
                state.render_validation_report.model_dump(mode="json")
                if state.render_validation_report is not None
                else None
            ),
            "timeline_path": f"{state.production_session.root_dir}/timeline.json",
        }
    )
    return view


def _events_view(state: ShortVideoProductionState) -> dict[str, Any]:
    view = _base_view(state, "events")
    view.update(
        {
            "events": [item.model_dump(mode="json") for item in state.production_events],
            "events_path": f"{state.production_session.root_dir}/events.jsonl",
        }
    )
    return view


def _quality_view(state: ShortVideoProductionState) -> dict[str, Any]:
    view = _base_view(state, "quality")
    view.update(
        {
            "quality_report": (
                state.quality_report.model_dump(mode="json")
                if state.quality_report is not None
                else None
            ),
            "quality_report_path": f"{state.production_session.root_dir}/quality_report.json",
            "quality_report_markdown_path": f"{state.production_session.root_dir}/quality_report.md",
            "render_validation_report": (
                state.render_validation_report.model_dump(mode="json")
                if state.render_validation_report is not None
                else None
            ),
        }
    )
    return view


def _artifacts_view(state: ShortVideoProductionState) -> dict[str, Any]:
    view = _base_view(state, "artifacts")
    view.update(
        {
            "artifacts": [item.model_dump(mode="json") for item in state.artifacts],
            "shot_artifacts": [item.model_dump(mode="json") for item in state.shot_artifacts],
            "asset_manifest": [item.model_dump(mode="json") for item in state.asset_manifest],
            "audio_manifest": [item.model_dump(mode="json") for item in state.audio_manifest],
            "quality_report_path": (
                state.quality_report.report_path
                if state.quality_report is not None
                else f"{state.production_session.root_dir}/quality_report.json"
            ),
            "final_dir": f"{state.production_session.root_dir}/final",
        }
    )
    return view


def _brief_summary(user_prompt: str) -> str:
    prompt = str(user_prompt or "").strip()
    return prompt or "Short-video production."


def _build_short_video_storyboard(
    *,
    user_prompt: str,
    reference_assets: list[ReferenceAssetEntry],
    selected_ratio: str | None,
    duration_seconds: float,
    video_type: str,
) -> ShortVideoStoryboard:
    """Build a deterministic P1a storyboard from the user brief and references."""
    brief = _brief_summary(user_prompt)
    normalized_video_type = _normalize_video_type(video_type) or "product_ad"
    reference_asset_ids = [item.reference_asset_id for item in reference_assets]
    global_constraints = _storyboard_global_constraints(brief, reference_assets)
    if normalized_video_type == "cartoon_short_drama":
        shots = _cartoon_storyboard_shots(
            brief=brief,
            duration_seconds=duration_seconds,
            reference_asset_ids=reference_asset_ids,
        )
        title = "Cartoon short-drama storyboard"
    elif normalized_video_type == "social_media_short":
        shots = _social_storyboard_shots(
            brief=brief,
            duration_seconds=duration_seconds,
            reference_asset_ids=reference_asset_ids,
        )
        title = "Social-media short storyboard"
    else:
        shots = _product_ad_storyboard_shots(
            brief=brief,
            duration_seconds=duration_seconds,
            reference_asset_ids=reference_asset_ids,
        )
        title = "Product-ad storyboard"

    return ShortVideoStoryboard(
        video_type=normalized_video_type,  # type: ignore[arg-type]
        title=title,
        narrative_summary=_storyboard_summary(normalized_video_type, brief),
        target_duration_seconds=duration_seconds,
        selected_ratio=selected_ratio,  # type: ignore[arg-type]
        global_constraints=global_constraints,
        reference_asset_ids=reference_asset_ids,
        shots=shots,
    )


def _storyboard_global_constraints(
    brief: str,
    reference_assets: list[ReferenceAssetEntry],
) -> list[str]:
    constraints: list[str] = [
        "Confirm storyboard before provider-specific asset planning.",
        "Keep final provider calls behind explicit user approval.",
    ]
    if reference_assets:
        constraints.append("Use uploaded reference assets as identity anchors.")
    if _requests_no_subtitles(brief):
        constraints.append("Do not render subtitles or on-screen captions.")
    if _extract_dialogue_lines(brief):
        constraints.append("Treat speaker-labelled lines as character dialogue, not narration.")
    if any(token in brief.lower() for token in ("语音", "声音", "voice", "audio", "bgm", "音乐")):
        constraints.append("Generate synchronized native audio that matches the requested voice style.")
    return constraints


def _storyboard_summary(video_type: str, brief: str) -> str:
    label = _video_type_label(video_type)
    return f"{label} storyboard based on the user brief: {_build_voiceover_text(brief)}"


def _product_ad_storyboard_shots(
    *,
    brief: str,
    duration_seconds: float,
    reference_asset_ids: list[str],
) -> list[ShortVideoStoryboardShot]:
    durations = _split_storyboard_durations(duration_seconds, 3)
    return [
        ShortVideoStoryboardShot(
            sequence_index=1,
            duration_seconds=durations[0],
            purpose="Hook and product reveal",
            visual_beat="Open with a polished product hero moment that makes the product immediately recognizable.",
            audio_notes="Short premium opening line or product sound cue.",
            constraints=["Show the product clearly in the first beat."],
            reference_asset_ids=reference_asset_ids,
        ),
        ShortVideoStoryboardShot(
            sequence_index=2,
            duration_seconds=durations[1],
            purpose="Benefit demonstration",
            visual_beat="Show the main selling points through natural, concrete product-use imagery instead of dense text.",
            audio_notes="Concise voiceover covering the strongest benefits from the brief.",
            constraints=["Keep benefit language selective and readable."],
            reference_asset_ids=reference_asset_ids,
        ),
        ShortVideoStoryboardShot(
            sequence_index=3,
            duration_seconds=durations[2],
            purpose="Trust close and call to action",
            visual_beat="End on a clean product packshot or usage result with a calm purchase or follow-up cue.",
            audio_notes="Soft closing line with brand or action cue if provided.",
            constraints=["Do not invent unsupported claims beyond the brief."],
            reference_asset_ids=reference_asset_ids,
        ),
    ]


def _cartoon_storyboard_shots(
    *,
    brief: str,
    duration_seconds: float,
    reference_asset_ids: list[str],
) -> list[ShortVideoStoryboardShot]:
    dialogue_lines = _extract_dialogue_lines(brief)
    durations = _split_storyboard_durations(duration_seconds, 3)
    if dialogue_lines:
        first_dialogue = dialogue_lines[: max(1, len(dialogue_lines) // 2)]
        second_dialogue = dialogue_lines[max(1, len(dialogue_lines) // 2):]
    else:
        first_dialogue = []
        second_dialogue = []
    return [
        ShortVideoStoryboardShot(
            sequence_index=1,
            duration_seconds=durations[0],
            purpose="Character setup and opening dialogue",
            visual_beat="Introduce the main cartoon characters facing each other with clear expressions and readable staging.",
            dialogue_lines=first_dialogue,
            audio_notes="Use character voices matching the requested cute or comedic tone.",
            constraints=["Dialogue belongs to characters, not an off-screen narrator."],
            reference_asset_ids=reference_asset_ids,
        ),
        ShortVideoStoryboardShot(
            sequence_index=2,
            duration_seconds=durations[1],
            purpose="Punchline or reveal",
            visual_beat="Hold on the characters as the misunderstanding or punchline lands.",
            dialogue_lines=second_dialogue,
            audio_notes="Keep timing tight and let the final line land clearly.",
            constraints=["Preserve the user-provided joke structure."],
            reference_asset_ids=reference_asset_ids,
        ),
        ShortVideoStoryboardShot(
            sequence_index=3,
            duration_seconds=durations[2],
            purpose="Reaction beat",
            visual_beat="Add a one-second pause or mutual stare when requested, then a clear exaggerated comedic reaction.",
            audio_notes="Use synchronized reaction sound effects and laughter if appropriate.",
            constraints=["Keep reaction timing visible even inside one generated clip."],
            reference_asset_ids=reference_asset_ids,
        ),
    ]


def _social_storyboard_shots(
    *,
    brief: str,
    duration_seconds: float,
    reference_asset_ids: list[str],
) -> list[ShortVideoStoryboardShot]:
    durations = _split_storyboard_durations(duration_seconds, 3)
    return [
        ShortVideoStoryboardShot(
            sequence_index=1,
            duration_seconds=durations[0],
            purpose="Opening hook",
            visual_beat="Start with the strongest surprising, useful, or visually clear moment from the brief.",
            audio_notes="Fast hook line or attention-grabbing sound cue.",
            constraints=["The first beat must be understandable without background context."],
            reference_asset_ids=reference_asset_ids,
        ),
        ShortVideoStoryboardShot(
            sequence_index=2,
            duration_seconds=durations[1],
            purpose="Main value or contrast",
            visual_beat="Deliver the central comparison, tutorial step, transformation, or social-media payoff.",
            audio_notes="Keep voiceover short and platform-friendly.",
            constraints=["Prioritize rhythm and clarity over exhaustive detail."],
            reference_asset_ids=reference_asset_ids,
        ),
        ShortVideoStoryboardShot(
            sequence_index=3,
            duration_seconds=durations[2],
            purpose="CTA or memorable close",
            visual_beat="End with a clean visual payoff and a simple action cue suitable for short-form feeds.",
            audio_notes="Brief closing phrase or music resolve.",
            constraints=["CTA should remain editable in later iterations."],
            reference_asset_ids=reference_asset_ids,
        ),
    ]


def _split_storyboard_durations(total_seconds: float, count: int) -> list[float]:
    """Split a target duration into readable storyboard shot durations."""
    if count <= 0:
        return []
    total = max(float(total_seconds or 0), float(count))
    base = round(total / count, 2)
    durations = [base for _ in range(count)]
    durations[-1] = round(max(1.0, total - sum(durations[:-1])), 2)
    return durations


def _build_short_video_asset_plan(
    *,
    user_prompt: str,
    reference_assets: list[ReferenceAssetEntry],
    selected_ratio: str | None,
    duration_seconds: float,
    video_type: str,
    video_provider: str = "seedance",
    video_model_name: str | None = None,
    video_resolution: str | None = None,
    storyboard: ShortVideoStoryboard | None = None,
) -> ShortVideoAssetPlan:
    brief = _brief_summary(user_prompt)
    normalized_video_type = _normalize_video_type(video_type) or "product_ad"
    normalized_provider = _normalize_short_video_provider(video_provider)
    normalized_model_name = _normalize_short_video_model_name(normalized_provider, video_model_name)
    normalized_resolution = _normalize_short_video_resolution(
        normalized_provider,
        normalized_model_name,
        video_resolution,
    )
    ratio_options = _ratio_options_for_provider(normalized_provider)
    effective_selected_ratio = selected_ratio if selected_ratio in ratio_options else None
    reference_asset_ids = [item.reference_asset_id for item in reference_assets]
    shot_plan = ShortVideoShotPlan(
        duration_seconds=duration_seconds,
        visual_prompt=_build_visual_prompt(normalized_video_type, brief, reference_assets, storyboard),
        voiceover_text=_build_voiceover_text_from_storyboard(brief, storyboard),
        reference_asset_ids=reference_asset_ids,
    )
    return ShortVideoAssetPlan(
        video_type=normalized_video_type,  # type: ignore[arg-type]
        planned_video_provider=normalized_provider,  # type: ignore[arg-type]
        planned_video_model_name=normalized_model_name,
        planned_video_resolution=normalized_resolution,
        planned_generate_audio=normalized_provider == "seedance",
        planned_tts=normalized_provider == "veo",
        planned_tts_provider="bytedance_tts" if normalized_provider == "veo" else "seedance_native_audio",
        ratio_options=ratio_options,  # type: ignore[arg-type]
        selected_ratio=effective_selected_ratio,  # type: ignore[arg-type]
        duration_seconds=duration_seconds,
        reference_asset_ids=reference_asset_ids,
        shot_plan=shot_plan,
    )


def _asset_plan_provider_settings(asset_plan: ShortVideoAssetPlan | None) -> tuple[str, str, str]:
    """Return existing provider settings from an asset plan, or normalized defaults."""
    if asset_plan is None:
        provider = "seedance"
        model_name = _normalize_short_video_model_name(provider, None)
        return provider, model_name, _normalize_short_video_resolution(provider, model_name, None)
    provider = _normalize_short_video_provider(asset_plan.planned_video_provider)
    model_name = _normalize_short_video_model_name(provider, asset_plan.planned_video_model_name)
    resolution = _normalize_short_video_resolution(provider, model_name, asset_plan.planned_video_resolution)
    return provider, model_name, resolution


def _build_visual_prompt(
    video_type: str,
    brief: str,
    reference_assets: list[ReferenceAssetEntry],
    storyboard: ShortVideoStoryboard | None = None,
) -> str:
    storyboard_instruction = _storyboard_prompt_instruction(storyboard)
    if video_type == "cartoon_short_drama":
        return _build_cartoon_short_drama_visual_prompt(brief, reference_assets, storyboard_instruction)
    if video_type == "social_media_short":
        return _build_social_media_visual_prompt(brief, reference_assets, storyboard_instruction)
    return _build_product_ad_visual_prompt(brief, reference_assets, storyboard_instruction)


def _build_product_ad_visual_prompt(
    brief: str,
    reference_assets: list[ReferenceAssetEntry],
    storyboard_instruction: str = "",
) -> str:
    reference_note = (
        "Use the provided product reference assets as identity anchors."
        if reference_assets
        else "No product reference image is available; infer visual identity from the brief."
    )
    return render_prompt_template(
        "product_ad_visual",
        {
            "brief": brief,
            "reference_note": reference_note,
            "storyboard_instruction": storyboard_instruction,
            "native_audio_instruction": _build_native_audio_instruction(brief),
        },
    )


def _build_cartoon_short_drama_visual_prompt(
    brief: str,
    reference_assets: list[ReferenceAssetEntry],
    storyboard_instruction: str = "",
) -> str:
    reference_note = (
        "Use the provided reference assets as character, product, or style anchors."
        if reference_assets
        else "Infer character and style identity from the brief."
    )
    return render_prompt_template(
        "cartoon_short_drama_visual",
        {
            "brief": brief,
            "reference_note": reference_note,
            "storyboard_instruction": storyboard_instruction,
            "native_audio_instruction": _build_native_audio_instruction(brief),
        },
    )


def _build_social_media_visual_prompt(
    brief: str,
    reference_assets: list[ReferenceAssetEntry],
    storyboard_instruction: str = "",
) -> str:
    reference_note = (
        "Use the provided reference assets as identity or style anchors."
        if reference_assets
        else "Infer the visual identity from the brief."
    )
    return render_prompt_template(
        "social_media_visual",
        {
            "brief": brief,
            "reference_note": reference_note,
            "storyboard_instruction": storyboard_instruction,
            "native_audio_instruction": _build_native_audio_instruction(brief),
        },
    )


def _storyboard_prompt_instruction(storyboard: ShortVideoStoryboard | None) -> str:
    """Return compact storyboard guidance for the provider prompt."""
    if storyboard is None or not storyboard.shots:
        return ""
    shot_summaries = []
    for shot in storyboard.shots:
        dialogue = f" Dialogue: {'; '.join(shot.dialogue_lines)}." if shot.dialogue_lines else ""
        constraints = f" Constraints: {'; '.join(shot.constraints)}." if shot.constraints else ""
        shot_summaries.append(
            f"Shot {shot.sequence_index}: {shot.purpose}. Visual: {shot.visual_beat}.{dialogue}{constraints}"
        )
    global_constraints = (
        f"Global constraints: {'; '.join(storyboard.global_constraints)}."
        if storyboard.global_constraints
        else ""
    )
    return render_prompt_template(
        "storyboard_instruction",
        {
            "shot_summaries": " ".join(shot_summaries),
            "global_constraints": global_constraints,
        },
    )


def _build_native_audio_instruction(brief: str) -> str:
    """Build Seedance-oriented audio guidance from user-provided brief text."""
    dialogue_lines = _extract_dialogue_lines(brief)
    subtitle_note = (
        "Do not render subtitles or on-screen captions."
        if _requests_no_subtitles(brief)
        else "Do not add subtitles unless the brief explicitly asks for them."
    )
    if dialogue_lines:
        return render_prompt_template(
            "native_audio_dialogue",
            {
                "dialogue_lines": "; ".join(dialogue_lines),
                "subtitle_note": subtitle_note,
            },
        )
    return render_prompt_template(
        "native_audio_scene",
        {
            "subtitle_note": subtitle_note,
        },
    )


def _extract_dialogue_lines(brief: str) -> list[str]:
    """Return speaker-attributed dialogue lines from plain text such as `Cat A: hello`."""
    lines: list[str] = []
    for raw_line in str(brief or "").splitlines():
        match = re.match(r"^\s*([^:：\n]{1,24})\s*[:：]\s*(.+?)\s*$", raw_line)
        if not match:
            continue
        speaker = match.group(1).strip()
        text = match.group(2).strip().strip('"')
        if not speaker or not text or not _looks_like_dialogue_speaker(speaker):
            continue
        safe_text = text.replace('"', "'")
        lines.append(f'{speaker} says "{safe_text}"')
    return lines


def _looks_like_dialogue_speaker(label: str) -> bool:
    """Return whether a colon-prefixed label looks like a speaker rather than a field name."""
    normalized = str(label or "").strip().lower()
    field_labels = {
        "品牌",
        "产品",
        "卖点",
        "价格",
        "时长",
        "风格",
        "语音风格",
        "字幕",
        "背景",
        "场景",
        "任务",
        "标题",
        "brief",
        "style",
        "product",
        "brand",
    }
    if normalized in field_labels:
        return False
    speaker_markers = (
        "角色",
        "人物",
        "猫",
        "狗",
        "男",
        "女",
        "旁白",
        "主播",
        "a",
        "b",
        "character",
        "narrator",
        "cat",
        "dog",
        "host",
    )
    if any(marker in normalized for marker in speaker_markers):
        return True
    return bool(re.fullmatch(r"[a-z0-9一二三四五六七八九十]{1,4}", normalized))


def _requests_no_subtitles(brief: str) -> bool:
    """Return whether the brief asks not to show subtitles or captions."""
    normalized = str(brief or "").lower()
    return any(token in normalized for token in ("不用显示字幕", "不要字幕", "无字幕", "no subtitles", "no captions"))


def _requested_short_video_provider(payload: dict[str, Any]) -> str:
    """Return the explicitly requested short-video provider or the default."""
    provider_hint = _requested_provider_hint(payload)
    if provider_hint:
        return _normalize_short_video_provider(provider_hint, strict=True)
    model_hint = _requested_model_hint(payload)
    if model_hint == VIDEO_GENERATION_VEO_MODEL_NAME:
        return "veo"
    return "seedance"


def _requested_video_model_name(payload: dict[str, Any], provider: str) -> str:
    """Return the provider-specific video model requested by render settings."""
    model_hint = _requested_model_hint(payload)
    if model_hint:
        return _normalize_short_video_model_name(provider, model_hint, strict=True)
    provider_hint = _requested_provider_hint(payload)
    if provider == "seedance" and provider_hint and "fast" in provider_hint:
        return VIDEO_GENERATION_SEEDANCE_2_FAST_MODEL_NAME
    return _normalize_short_video_model_name(provider, None)


def _requested_video_resolution(payload: dict[str, Any], provider: str, model_name: str) -> str:
    """Return the provider-specific video resolution requested by render settings."""
    for key in ("resolution", "video_resolution", "seedance_resolution"):
        value = str(payload.get(key, "") or "").strip()
        if value:
            return _normalize_short_video_resolution(
                provider,
                model_name,
                value,
                strict=_normalize_short_video_provider(provider) == "veo",
            )
    return _normalize_short_video_resolution(provider, model_name, None)


def _requested_provider_hint(payload: dict[str, Any]) -> str:
    """Return the raw provider/runtime hint from render settings."""
    for key in ("provider", "video_provider", "runtime", "provider_runtime"):
        value = str(payload.get(key, "") or "").strip().lower()
        if value:
            return value
    return ""


def _requested_model_hint(payload: dict[str, Any]) -> str:
    """Return the raw video model hint from render settings."""
    for key in ("model_name", "seedance_model_name", "video_model_name"):
        value = str(payload.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _normalize_short_video_provider(value: Any, *, strict: bool = False) -> str:
    """Normalize short-video production provider names without silent fallback."""
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_").replace("+", "_")
    aliases = {
        "": "seedance",
        "seedance": "seedance",
        "seedance2": "seedance",
        "seedance_2": "seedance",
        "seedance_2_0": "seedance",
        "seedance_fast": "seedance",
        "seedance_2_fast": "seedance",
        "seedance_2_0_fast": "seedance",
        "seedance_native_audio": "seedance",
        "doubao_seedance_2_0_260128": "seedance",
        "doubao_seedance_2_0_fast_260128": "seedance",
        "veo": "veo",
        "veo_tts": "veo",
        "veotts": "veo",
        "veo3": "veo",
        "veo_3": "veo",
        "veo_3_1": "veo",
    }
    if normalized in aliases:
        return aliases[normalized]
    if strict:
        raise ValueError(
            "Unsupported short-video provider. Supported providers are seedance, seedance_fast, and veo_tts."
        )
    return "seedance"


def _normalize_short_video_model_name(
    provider: str,
    value: Any,
    *,
    strict: bool = False,
) -> str:
    """Normalize a provider-specific model name without changing provider."""
    normalized_provider = _normalize_short_video_provider(provider)
    if normalized_provider == "veo":
        model_name = str(value or "").strip()
        if not model_name:
            return VIDEO_GENERATION_VEO_MODEL_NAME
        if model_name == VIDEO_GENERATION_VEO_MODEL_NAME:
            return model_name
        if strict:
            raise ValueError(
                f"Unsupported Veo model for short-video production: {model_name}. "
                f"Supported model: {VIDEO_GENERATION_VEO_MODEL_NAME}."
            )
        return VIDEO_GENERATION_VEO_MODEL_NAME
    if strict:
        model_name = str(value or "").strip()
        if model_name and normalize_seedance_model_name(model_name) != model_name:
            raise ValueError(
                f"Unsupported Seedance model for short-video production: {model_name}."
            )
    return normalize_seedance_model_name(value)


def _normalize_short_video_resolution(
    provider: str,
    model_name: str,
    value: Any,
    *,
    strict: bool = False,
) -> str:
    """Normalize provider-specific resolution settings."""
    normalized_provider = _normalize_short_video_provider(provider)
    resolution = str(value or "").strip()
    if normalized_provider == "veo":
        if not resolution:
            return "720p"
        if resolution == "720p":
            return resolution
        if strict:
            raise ValueError(
                f"Unsupported Veo+TTS resolution for short-video production: {resolution}. "
                "The current compatible runtime supports 720p only."
            )
        return "720p"
    if strict and resolution:
        normalized = normalize_seedance_video_resolution(model_name, resolution)
        if normalized != resolution:
            raise ValueError(
                f"Unsupported Seedance resolution for short-video production: {resolution}."
            )
    return normalize_seedance_video_resolution(model_name, resolution or None)


def _ratio_options_for_provider(provider: str) -> list[str]:
    """Return user-selectable aspect ratios for one short-video provider."""
    if _normalize_short_video_provider(provider) == "veo":
        return ["9:16", "16:9"]
    return ["9:16", "16:9", "1:1"]


def _requested_video_type(user_prompt: str, payload: dict[str, Any]) -> str:
    for key in ("video_type", "short_video_type", "production_type", "project_type"):
        normalized = _normalize_video_type(payload.get(key))
        if normalized:
            return normalized
    return _infer_video_type_from_text(user_prompt)


def _infer_video_type_from_text(text: str) -> str:
    normalized = str(text or "").strip().lower()
    if _extract_dialogue_lines(text) or any(token in normalized for token in ("对话", "dialogue", "conversation")):
        return "cartoon_short_drama"
    if any(token in normalized for token in ("卡通短剧", "动画短剧", "cartoon", "animated short", "short drama")):
        return "cartoon_short_drama"
    if any(token in normalized for token in ("社交媒体", "小红书", "抖音", "tiktok", "reels", "shorts", "social media")):
        return "social_media_short"
    return "product_ad"


def _normalize_video_type(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "product": "product_ad",
        "product_ad": "product_ad",
        "product_ads": "product_ad",
        "ad": "product_ad",
        "advertising": "product_ad",
        "cartoon": "cartoon_short_drama",
        "cartoon_drama": "cartoon_short_drama",
        "cartoon_short": "cartoon_short_drama",
        "cartoon_short_drama": "cartoon_short_drama",
        "animated_short": "cartoon_short_drama",
        "short_drama": "cartoon_short_drama",
        "social": "social_media_short",
        "social_short": "social_media_short",
        "social_media": "social_media_short",
        "social_media_short": "social_media_short",
    }
    if normalized in aliases:
        return aliases[normalized]
    return normalized if normalized in _VIDEO_TYPES else ""


def _video_type_label(video_type: str) -> str:
    return _VIDEO_TYPE_LABELS.get(_normalize_video_type(video_type), "short-video")


def _provider_label(asset_plan: ShortVideoAssetPlan) -> str:
    """Return a user-readable provider label for review payloads."""
    if asset_plan.planned_video_provider == "veo":
        return "Veo + ByteDance TTS"
    if asset_plan.planned_video_model_name == VIDEO_GENERATION_SEEDANCE_2_FAST_MODEL_NAME:
        return "Seedance 2.0 fast native audio"
    return "Seedance 2.0 native audio"


def _provider_notes(asset_plan: ShortVideoAssetPlan) -> list[str]:
    """Return concise provider constraints for user review."""
    if asset_plan.planned_video_provider == "veo":
        return [
            "Compatible runtime: Veo generates video and ByteDance TTS generates voiceover.",
            "Current Veo+TTS path supports 9:16 or 16:9, 720p, and 4/6/8-second provider segments.",
            "The system will not auto-switch away from Veo if this provider fails.",
        ]
    notes = [
        "Default runtime: Seedance generates synchronized video, dialogue, sound effects, and music natively.",
        "The system will not auto-switch to another provider if Seedance fails.",
    ]
    if asset_plan.planned_video_model_name == VIDEO_GENERATION_SEEDANCE_2_FAST_MODEL_NAME:
        notes.append("Seedance 2.0 fast is kept at 720p because it does not support 1080p.")
    return notes


def _build_voiceover_text(brief: str) -> str:
    cleaned = " ".join(brief.split())
    if len(cleaned) <= 180:
        return cleaned
    return f"{cleaned[:177].rstrip()}..."


def _build_voiceover_text_from_storyboard(
    brief: str,
    storyboard: ShortVideoStoryboard | None,
) -> str:
    """Return concise voiceover/dialogue guidance from the approved storyboard."""
    if storyboard is None:
        return _build_voiceover_text(brief)
    dialogue_lines = [
        line
        for shot in storyboard.shots
        for line in shot.dialogue_lines
        if str(line or "").strip()
    ]
    if dialogue_lines:
        return " | ".join(dialogue_lines)
    audio_notes = [
        shot.audio_notes
        for shot in storyboard.shots
        if str(shot.audio_notes or "").strip()
    ]
    if audio_notes:
        return _build_voiceover_text(" ".join(audio_notes))
    return _build_voiceover_text(brief)


def _valid_reference_assets(state: ShortVideoProductionState) -> list[ReferenceAssetEntry]:
    return [item for item in state.reference_assets if item.status == "valid"]


def _replacement_reference_id(response: dict[str, Any]) -> str:
    for key in ("replace_reference_asset_id", "replace_reference_id", "replaces"):
        value = str(response.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _mark_reference_replaced(
    state: ShortVideoProductionState,
    *,
    replace_reference_id: str,
    replacement_reference_id: str,
) -> list[str]:
    replaced: list[str] = []
    for reference in state.reference_assets:
        if reference.reference_asset_id == replace_reference_id and reference.status == "valid":
            reference.status = "replaced"
            reference.replaced_by = replacement_reference_id
            replaced.append(reference.reference_asset_id)
    return replaced


def _mark_generated_outputs_stale(
    state: ShortVideoProductionState,
    *,
    reason: str,
    artifact_notice: str = "May be stale after production inputs changed.",
) -> None:
    for asset in state.asset_manifest:
        if asset.status == "valid":
            asset.status = "stale"
            asset.stale_reason = reason
    for audio in state.audio_manifest:
        if audio.status == "valid":
            audio.status = "stale"
            audio.stale_reason = reason
    for artifact in state.artifacts:
        if "stale" not in artifact.description.lower():
            artifact.description = f"{artifact.description} {artifact_notice}".strip()
    state.timeline = None
    state.render_report = None
    state.render_validation_report = None
    state.quality_report = None


def _revision_notes_from_response(response: dict[str, Any]) -> str:
    for key in ("notes", "message", "revision_notes", "revision_request"):
        value = str(response.get(key, "") or "").strip()
        if value:
            return value
    return ""


def _revision_target_kinds(impact_view: dict[str, Any]) -> set[str]:
    kinds = {
        str(target.get("kind", "") or "").strip()
        for target in impact_view.get("matched_targets", [])
        if isinstance(target, dict)
    }
    return kinds or {"production"}


def _append_revision_note(brief_summary: str, notes: str) -> str:
    base = str(brief_summary or "").strip() or "Short-video production."
    return f"{base}\n\nRevision notes: {notes}"


def _apply_revision_to_asset_plan(
    state: ShortVideoProductionState,
    *,
    notes: str,
    target_kinds: set[str],
    matched_targets: list[dict[str, Any]],
) -> None:
    asset_plan = state.asset_plan
    if asset_plan is None:
        return

    if target_kinds & {"shot_asset_plan", "shot_artifact"}:
        if _apply_targeted_shot_revision(
            state,
            notes=notes,
            target_kinds=target_kinds,
            matched_targets=matched_targets,
        ):
            asset_plan.status = "draft"
            return

    if target_kinds <= {"voiceover"}:
        asset_plan.plan_id = new_id("asset_plan")
        asset_plan.shot_plan.voiceover_text = notes
        asset_plan.status = "draft"
        return

    state.asset_plan = _build_short_video_asset_plan(
        user_prompt=state.brief_summary,
        reference_assets=_valid_reference_assets(state),
        selected_ratio=asset_plan.selected_ratio,
        duration_seconds=asset_plan.duration_seconds,
        video_type=asset_plan.video_type,
        video_provider=asset_plan.planned_video_provider,
        video_model_name=asset_plan.planned_video_model_name,
        video_resolution=asset_plan.planned_video_resolution,
        storyboard=state.storyboard,
    )
    state.shot_asset_plans = _build_shot_asset_plans(
        storyboard=state.storyboard,
        asset_plan=state.asset_plan,
    )


def _apply_targeted_shot_revision(
    state: ShortVideoProductionState,
    *,
    notes: str,
    target_kinds: set[str],
    matched_targets: list[dict[str, Any]],
) -> bool:
    """Apply revision notes to targeted shot segment plans only."""
    target_plan_ids = _targeted_shot_asset_plan_ids(state, matched_targets)
    if not target_plan_ids:
        review_artifact = _current_review_shot_artifact(state)
        if review_artifact is not None:
            target_plan_ids.add(review_artifact.shot_asset_plan_id)
    if not target_plan_ids:
        return False

    for plan in state.shot_asset_plans:
        if plan.shot_asset_plan_id in target_plan_ids:
            _apply_notes_to_shot_segment_plan(
                plan,
                notes=notes,
                target_kinds=target_kinds,
            )
    for artifact in state.shot_artifacts:
        if artifact.shot_asset_plan_id in target_plan_ids:
            artifact.status = "stale"
            artifact.metadata["stale_reason"] = f"Revision applied: {notes}"
            _mark_shot_artifact_media_stale(
                state,
                artifact,
                reason=f"Revision applied: {notes}",
            )
    return True


def _targeted_shot_asset_plan_ids(
    state: ShortVideoProductionState,
    matched_targets: list[dict[str, Any]],
) -> set[str]:
    """Return shot segment ids selected by revision targets."""
    plan_ids: set[str] = set()
    for target in matched_targets:
        kind = str(target.get("kind", "") or "").strip()
        target_id = str(target.get("id", "") or "").strip()
        if kind == "shot_asset_plan" and target_id:
            plan_ids.add(target_id)
        elif kind == "shot_artifact" and target_id:
            artifact = _shot_artifact_by_id(state, target_id)
            if artifact is not None:
                plan_ids.add(artifact.shot_asset_plan_id)
        elif kind == "shot" and target_id:
            plan_ids.update(
                plan.shot_asset_plan_id
                for plan in state.shot_asset_plans
                if target_id in plan.storyboard_shot_ids
            )
    return plan_ids


def _apply_notes_to_shot_segment_plan(
    segment_plan: ShortVideoShotAssetPlan,
    *,
    notes: str,
    target_kinds: set[str],
) -> None:
    """Attach user revision notes to one segment plan and mark it pending."""
    if notes:
        segment_plan.visual_prompt = _append_segment_revision_note(
            segment_plan.visual_prompt,
            notes,
        )
        if target_kinds & {"voiceover"}:
            segment_plan.voiceover_text = notes
    segment_plan.status = "draft"


def _append_segment_revision_note(base_text: str, notes: str) -> str:
    base = str(base_text or "").strip()
    note = str(notes or "").strip()
    if not note:
        return base
    if note in base:
        return base
    return f"{base}\nSegment revision request: {note}".strip()


def _mark_revision_outputs_stale(
    state: ShortVideoProductionState,
    *,
    impacted: list[dict[str, Any]],
    reason: str,
) -> None:
    impacted_kinds = {
        str(item.get("kind", "") or "").strip()
        for item in impacted
        if isinstance(item, dict)
    }
    if not impacted_kinds:
        return
    impacted_ids = _impacted_ids_by_kind(impacted)

    if "video_asset" in impacted_kinds:
        video_ids = impacted_ids.get("video_asset", set())
        for asset in state.asset_manifest:
            if asset.kind == "video" and asset.status == "valid" and (not video_ids or asset.asset_id in video_ids):
                asset.status = "stale"
                asset.stale_reason = reason
    if "audio_asset" in impacted_kinds:
        audio_ids = impacted_ids.get("audio_asset", set())
        for audio in state.audio_manifest:
            if audio.status == "valid" and (not audio_ids or audio.audio_id in audio_ids):
                audio.status = "stale"
                audio.stale_reason = reason
    if "shot_asset_plan" in impacted_kinds:
        plan_ids = impacted_ids.get("shot_asset_plan", set())
        for plan in state.shot_asset_plans:
            if plan.status in {"approved", "generating", "generated", "reviewed"} and (
                not plan_ids or plan.shot_asset_plan_id in plan_ids
            ):
                plan.status = "stale"
    if "shot_artifact" in impacted_kinds:
        artifact_ids = impacted_ids.get("shot_artifact", set())
        for artifact in state.shot_artifacts:
            if artifact.status in {"generated", "approved"} and (
                not artifact_ids or artifact.shot_artifact_id in artifact_ids
            ):
                artifact.status = "stale"
                artifact.metadata["stale_reason"] = reason
    if impacted_kinds & {"timeline", "video_asset", "audio_asset", "shot_artifact"}:
        state.timeline = None
        state.render_report = None
        state.render_validation_report = None
        state.quality_report = None
    if "final_artifact" in impacted_kinds:
        state.quality_report = None
        for artifact in state.artifacts:
            if "stale" not in artifact.description.lower():
                artifact.description = (
                    f"{artifact.description} May be stale after revision."
                ).strip()


def _impacted_ids_by_kind(impacted: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Group revision-impact item ids by kind."""
    ids_by_kind: dict[str, set[str]] = {}
    for item in impacted:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "") or "").strip()
        item_id = str(item.get("id", "") or "").strip()
        if kind:
            ids_by_kind.setdefault(kind, set())
            if item_id:
                ids_by_kind[kind].add(item_id)
    return ids_by_kind


def _mark_shot_artifact_media_stale(
    state: ShortVideoProductionState,
    artifact: ShortVideoShotArtifact,
    *,
    reason: str,
) -> None:
    """Mark the generated media for one shot artifact stale."""
    media_asset_ids = {
        artifact.video_asset_id,
        str(artifact.metadata.get("preview_video_asset_id") or "").strip(),
    }
    media_asset_ids.discard("")
    for asset in state.asset_manifest:
        if asset.asset_id in media_asset_ids and asset.status == "valid":
            asset.status = "stale"
            asset.stale_reason = reason
    for audio in state.audio_manifest:
        if audio.audio_id == artifact.audio_id and audio.status == "valid":
            audio.status = "stale"
            audio.stale_reason = reason
    state.timeline = None
    state.render_report = None
    state.render_validation_report = None
    state.quality_report = None


def _mark_existing_generated_media_superseded(
    state: ShortVideoProductionState,
    *,
    reason: str,
) -> None:
    """Mark previous generated media stale before writing a new approved render."""
    for asset in state.asset_manifest:
        if asset.source in {"expert", "cache"} and asset.status == "valid":
            asset.status = "stale"
            asset.stale_reason = reason
    for audio in state.audio_manifest:
        if audio.source in {"expert", "cache"} and audio.status == "valid":
            audio.status = "stale"
            audio.stale_reason = reason
    state.timeline = None
    state.render_report = None
    state.render_validation_report = None
    state.quality_report = None


def _mark_existing_shot_outputs_stale(
    state: ShortVideoProductionState,
    *,
    reason: str,
) -> None:
    """Mark previous shot-level plans and previews stale before a new run."""
    for plan in state.shot_asset_plans:
        if plan.status in {"generated", "reviewed"}:
            plan.status = "stale"
    for artifact in state.shot_artifacts:
        if artifact.status in {"generated", "approved"}:
            artifact.status = "stale"
            artifact.metadata["stale_reason"] = reason
    state.shot_artifacts = []


def _build_shot_asset_plans(
    *,
    storyboard: ShortVideoStoryboard | None,
    asset_plan: ShortVideoAssetPlan,
) -> list[ShortVideoShotAssetPlan]:
    """Build provider-executable shot segments from a storyboard and asset plan."""
    if storyboard is None or not storyboard.shots:
        return [
            ShortVideoShotAssetPlan(
                segment_index=1,
                storyboard_shot_ids=[],
                storyboard_sequence_indexes=[],
                duration_seconds=_provider_segment_duration(asset_plan.duration_seconds),
                visual_prompt=asset_plan.shot_plan.visual_prompt,
                voiceover_text=asset_plan.shot_plan.voiceover_text,
                reference_asset_ids=asset_plan.reference_asset_ids,
                planned_video_provider=asset_plan.planned_video_provider,
                planned_video_model_name=asset_plan.planned_video_model_name,
                planned_video_resolution=asset_plan.planned_video_resolution,
                planned_generate_audio=asset_plan.planned_generate_audio,
                selected_ratio=asset_plan.selected_ratio,
                status="approved" if asset_plan.status == "approved" else "draft",
            )
        ]

    shot_groups = _group_storyboard_shots_for_provider(storyboard)
    return [
        _shot_asset_plan_from_group(
            asset_plan=asset_plan,
            storyboard=storyboard,
            segment_index=index,
            shots=shots,
        )
        for index, shots in enumerate(shot_groups, start=1)
    ]


def _group_storyboard_shots_for_provider(
    storyboard: ShortVideoStoryboard,
) -> list[list[ShortVideoStoryboardShot]]:
    """Group storyboard shots into provider-valid segments for Seedance constraints."""
    shots = list(storyboard.shots)
    if not shots:
        return []
    if storyboard.target_duration_seconds <= 10:
        return [shots]

    groups: list[list[ShortVideoStoryboardShot]] = []
    current: list[ShortVideoStoryboardShot] = []
    current_duration = 0.0
    for index, shot in enumerate(shots):
        shot_duration = max(1.0, float(shot.duration_seconds or 1.0))
        if current and current_duration + shot_duration > 15:
            groups.append(current)
            current = []
            current_duration = 0.0
        current.append(shot)
        current_duration += shot_duration
        remaining_duration = sum(
            max(1.0, float(item.duration_seconds or 1.0))
            for item in shots[index + 1:]
        )
        if current_duration >= 4 and (remaining_duration == 0 or remaining_duration >= 4):
            groups.append(current)
            current = []
            current_duration = 0.0
    if current:
        if groups and sum(float(item.duration_seconds or 1.0) for item in current) < 4:
            groups[-1].extend(current)
        else:
            groups.append(current)
    return groups


def _shot_asset_plan_from_group(
    *,
    asset_plan: ShortVideoAssetPlan,
    storyboard: ShortVideoStoryboard,
    segment_index: int,
    shots: list[ShortVideoStoryboardShot],
) -> ShortVideoShotAssetPlan:
    """Build one segment-level plan from one storyboard shot group."""
    duration_seconds = _provider_segment_duration(sum(float(shot.duration_seconds or 1.0) for shot in shots))
    visual_prompt = _shot_segment_visual_prompt(asset_plan, storyboard, segment_index, shots)
    voiceover_text = _shot_segment_voiceover_text(asset_plan, shots)
    return ShortVideoShotAssetPlan(
        segment_index=segment_index,
        storyboard_shot_ids=[shot.shot_id for shot in shots],
        storyboard_sequence_indexes=[shot.sequence_index for shot in shots],
        duration_seconds=duration_seconds,
        visual_prompt=visual_prompt,
        voiceover_text=voiceover_text,
        reference_asset_ids=asset_plan.reference_asset_ids,
        planned_video_provider=asset_plan.planned_video_provider,
        planned_video_model_name=asset_plan.planned_video_model_name,
        planned_video_resolution=asset_plan.planned_video_resolution,
        planned_generate_audio=asset_plan.planned_generate_audio,
        selected_ratio=asset_plan.selected_ratio,
        status="approved" if asset_plan.status == "approved" else "draft",
    )


def _provider_segment_duration(duration_seconds: float) -> float:
    """Return an integer Seedance-compatible segment duration."""
    try:
        value = float(duration_seconds)
    except (TypeError, ValueError):
        value = 4.0
    rounded = int(value)
    if value > rounded:
        rounded += 1
    return float(min(15, max(4, rounded)))


def _shot_segment_visual_prompt(
    asset_plan: ShortVideoAssetPlan,
    storyboard: ShortVideoStoryboard,
    segment_index: int,
    shots: list[ShortVideoStoryboardShot],
) -> str:
    """Return a compact provider prompt for one shot segment."""
    shot_parts = []
    for shot in shots:
        dialogue = f" Dialogue: {'; '.join(shot.dialogue_lines)}." if shot.dialogue_lines else ""
        constraints = f" Constraints: {'; '.join(shot.constraints)}." if shot.constraints else ""
        shot_parts.append(
            f"Storyboard shot {shot.sequence_index}: {shot.purpose}. Visual: {shot.visual_beat}.{dialogue}{constraints}"
        )
    global_constraints = (
        f"Global constraints: {'; '.join(storyboard.global_constraints)}."
        if storyboard.global_constraints
        else ""
    )
    return render_prompt_template(
        "shot_segment_visual",
        {
            "segment_index": segment_index,
            "covered_shots": ", ".join(str(shot.sequence_index) for shot in shots),
            "shot_parts": " ".join(shot_parts),
            "global_constraints": global_constraints,
            "full_asset_plan_prompt": asset_plan.shot_plan.visual_prompt,
        },
    )


def _shot_segment_voiceover_text(
    asset_plan: ShortVideoAssetPlan,
    shots: list[ShortVideoStoryboardShot],
) -> str:
    """Return dialogue or audio guidance scoped to one shot segment."""
    dialogue = [line for shot in shots for line in shot.dialogue_lines if str(line or "").strip()]
    if dialogue:
        return " | ".join(dialogue)
    audio_notes = [shot.audio_notes for shot in shots if str(shot.audio_notes or "").strip()]
    if audio_notes:
        return _build_voiceover_text(" ".join(audio_notes))
    return asset_plan.shot_plan.voiceover_text


def _asset_plan_for_shot_segment(
    asset_plan: ShortVideoAssetPlan,
    segment_plan: ShortVideoShotAssetPlan,
) -> ShortVideoAssetPlan:
    """Convert a segment plan into the existing provider runtime asset-plan shape."""
    return ShortVideoAssetPlan(
        plan_id=segment_plan.shot_asset_plan_id,
        video_type=asset_plan.video_type,
        planned_video_provider=segment_plan.planned_video_provider,
        planned_video_model_name=segment_plan.planned_video_model_name,
        planned_video_resolution=segment_plan.planned_video_resolution,
        planned_generate_audio=segment_plan.planned_generate_audio,
        planned_tts=asset_plan.planned_tts,
        planned_tts_provider=asset_plan.planned_tts_provider,
        ratio_options=asset_plan.ratio_options,
        selected_ratio=segment_plan.selected_ratio,
        duration_seconds=segment_plan.duration_seconds,
        reference_asset_ids=segment_plan.reference_asset_ids,
        shot_plan=ShortVideoShotPlan(
            shot_id=segment_plan.shot_asset_plan_id,
            duration_seconds=segment_plan.duration_seconds,
            visual_prompt=segment_plan.visual_prompt,
            voiceover_text=segment_plan.voiceover_text,
            reference_asset_ids=segment_plan.reference_asset_ids,
        ),
        status="approved",
    )


def _next_pending_shot_asset_plan(
    state: ShortVideoProductionState,
) -> ShortVideoShotAssetPlan | None:
    """Return the next provider segment that still needs generation."""
    for plan in sorted(state.shot_asset_plans, key=lambda item: item.segment_index):
        if plan.status in {"draft", "approved"}:
            return plan
    return None


def _has_partial_shot_regeneration_pending(state: ShortVideoProductionState) -> bool:
    """Return whether an existing segment plan is waiting for local regeneration."""
    if not state.shot_asset_plans:
        return False
    if not state.shot_artifacts:
        return False
    return any(plan.status == "draft" for plan in state.shot_asset_plans)


def _prepare_partial_shot_regeneration(
    state: ShortVideoProductionState,
    *,
    selected_ratio: str,
) -> None:
    """Prepare existing segment plans for gated partial regeneration."""
    for plan in state.shot_asset_plans:
        if plan.selected_ratio is None:
            plan.selected_ratio = selected_ratio  # type: ignore[assignment]
        if plan.status == "stale":
            plan.status = "draft"
    state.timeline = None
    state.render_report = None
    state.render_validation_report = None
    state.quality_report = None


def _shot_asset_plan_by_id(
    state: ShortVideoProductionState,
    shot_asset_plan_id: str,
) -> ShortVideoShotAssetPlan | None:
    """Return a shot asset plan by id."""
    for plan in state.shot_asset_plans:
        if plan.shot_asset_plan_id == shot_asset_plan_id:
            return plan
    return None


def _shot_artifact_by_id(
    state: ShortVideoProductionState,
    shot_artifact_id: str,
) -> ShortVideoShotArtifact | None:
    """Return a shot artifact by id."""
    for artifact in state.shot_artifacts:
        if artifact.shot_artifact_id == shot_artifact_id:
            return artifact
    return None


def _current_review_shot_artifact(
    state: ShortVideoProductionState,
) -> ShortVideoShotArtifact | None:
    """Return the latest generated shot artifact waiting for review."""
    for artifact in reversed(state.shot_artifacts):
        if artifact.status == "generated":
            return artifact
    return None


def _approved_shot_artifacts_in_plan_order(
    state: ShortVideoProductionState,
) -> list[ShortVideoShotArtifact]:
    """Return approved shot artifacts sorted by their segment plan order."""
    approved: list[ShortVideoShotArtifact] = []
    for plan in sorted(state.shot_asset_plans, key=lambda item: item.segment_index):
        candidates = [
            artifact
            for artifact in state.shot_artifacts
            if artifact.shot_asset_plan_id == plan.shot_asset_plan_id and artifact.status == "approved"
        ]
        if candidates:
            approved.append(candidates[-1])
    return approved


def _segment_progress(
    segment_plan: ShortVideoShotAssetPlan,
    plans: list[ShortVideoShotAssetPlan],
    *,
    base: int,
) -> int:
    """Return coarse progress for the active segment."""
    total = max(1, len(plans))
    segment_offset = int(((segment_plan.segment_index - 1) / total) * 40)
    return min(95, max(0, base + segment_offset))


def _shot_preview_output_path(session_root: Path, shot_asset_plan_id: str) -> Path:
    """Return the preview MP4 path for one generated shot segment."""
    safe_id = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in shot_asset_plan_id)
    return session_root / "renders" / f"{safe_id}_preview.mp4"


def _ensure_preview_assets_for_final_concat(
    state: ShortVideoProductionState,
    *,
    approved_artifacts: list[ShortVideoShotArtifact],
    render_settings: ShortVideoRenderSettings,
) -> None:
    """Register reviewed segment previews as self-contained video concat assets."""
    existing_ids = {asset.asset_id for asset in state.asset_manifest}
    for artifact in approved_artifacts:
        preview_asset_id = f"{artifact.shot_artifact_id}_preview_video"
        artifact.metadata["preview_video_asset_id"] = preview_asset_id
        if preview_asset_id in existing_ids:
            continue
        state.asset_manifest.append(
            AssetManifestEntry(
                asset_id=preview_asset_id,
                kind="video",
                path=artifact.preview_path,
                source="cache",
                provider="timeline_renderer",
                status="valid",
                depends_on=[artifact.video_asset_id, artifact.audio_id],
                duration_seconds=None,
                width=render_settings.width,
                height=render_settings.height,
                metadata={
                    "self_contained_preview": True,
                    "shot_artifact_id": artifact.shot_artifact_id,
                    "shot_asset_plan_id": artifact.shot_asset_plan_id,
                },
            )
        )
        existing_ids.add(preview_asset_id)


def _build_multi_clip_timeline(
    *,
    shot_artifacts: list[ShortVideoShotArtifact],
    shot_asset_plans: list[ShortVideoShotAssetPlan],
    render_settings: ShortVideoRenderSettings,
) -> ShortVideoTimeline:
    """Build a timeline from approved shot segment artifacts."""
    plan_by_id = {plan.shot_asset_plan_id: plan for plan in shot_asset_plans}
    video_clips: list[VideoClip] = []
    audio_clips: list[AudioClip] = []
    start_seconds = 0.0
    for artifact in shot_artifacts:
        plan = plan_by_id.get(artifact.shot_asset_plan_id)
        duration = float(plan.duration_seconds if plan is not None else 4.0)
        video_asset_id = str(artifact.metadata.get("preview_video_asset_id") or artifact.video_asset_id)
        video_clips.append(
            VideoClip(
                clip_id=f"clip_segment_{artifact.segment_index}_video",
                asset_id=video_asset_id,
                start_seconds=start_seconds,
                duration_seconds=duration,
            )
        )
        audio_clips.append(
            AudioClip(
                clip_id=f"clip_segment_{artifact.segment_index}_audio",
                audio_id=artifact.audio_id,
                start_seconds=start_seconds,
                duration_seconds=duration,
            )
        )
        start_seconds += duration
    return ShortVideoTimeline(
        timeline_id=new_id("timeline"),
        duration_seconds=round(start_seconds, 2),
        render_settings=render_settings,
        video_tracks=[VideoTrack(track_id="video_main", clips=video_clips)],
        audio_tracks=[AudioTrack(track_id="audio_main", kind="voiceover", clips=audio_clips)],
    )


def _final_output_path(session_root: Path, plan_id: str) -> Path:
    """Return a version-safe final MP4 path for one approved asset plan."""
    plan_segment = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in plan_id
    )
    return session_root / "final" / (plan_segment or new_id("asset_plan")) / "final.mp4"


def _shot_review_payload(
    state: ShortVideoProductionState,
    shot_artifact: ShortVideoShotArtifact,
) -> ReviewPayload:
    """Build a review payload for one generated shot segment preview."""
    segment_plan = _shot_asset_plan_by_id(state, shot_artifact.shot_asset_plan_id)
    total_segments = len(state.shot_asset_plans)
    sequence_indexes = (
        segment_plan.storyboard_sequence_indexes
        if segment_plan is not None
        else []
    )
    return ReviewPayload(
        review_type="shot_review",
        title=f"Review generated shot segment {shot_artifact.segment_index}",
        summary=(
            "A provider-generated shot segment is ready. Approve it to continue; "
            "revise it to return to the gated plan review before regeneration."
        ),
        items=[
            {
                "kind": "shot_segment",
                "shot_artifact_id": shot_artifact.shot_artifact_id,
                "shot_asset_plan_id": shot_artifact.shot_asset_plan_id,
                "segment_index": shot_artifact.segment_index,
                "total_segments": total_segments,
                "storyboard_sequence_indexes": sequence_indexes,
                "storyboard_shot_ids": shot_artifact.storyboard_shot_ids,
                "duration_seconds": segment_plan.duration_seconds if segment_plan is not None else None,
                "status": shot_artifact.status,
            },
            {
                "kind": "preview_artifact",
                "path": shot_artifact.preview_path,
                "video_asset_id": shot_artifact.video_asset_id,
                "audio_id": shot_artifact.audio_id,
            },
            {
                "kind": "questions",
                "questions": [
                    "Approve this generated segment to continue.",
                    "Revise this generated segment if motion, dialogue, product visibility, or style is wrong.",
                    "Cancel production if the direction is no longer useful.",
                ],
            },
        ],
        options=[
            {"decision": "approve", "label": "Approve segment and continue"},
            {"decision": "revise", "label": "Revise segment before continuing"},
            {"decision": "cancel", "label": "Cancel production"},
        ],
    )


def _storyboard_review_payload(state: ShortVideoProductionState) -> ReviewPayload:
    storyboard = state.storyboard
    if storyboard is None:
        return ReviewPayload(
            review_type="storyboard_review",
            title="Review short-video storyboard",
            summary=state.brief_summary,
            items=[],
            options=_storyboard_review_options(),
        )
    return ReviewPayload(
        review_type="storyboard_review",
        title=f"Review {_video_type_label(storyboard.video_type)} storyboard",
        summary=storyboard.narrative_summary,
        items=[
            {
                "kind": "video_type",
                "video_type": storyboard.video_type,
                "label": _video_type_label(storyboard.video_type),
            },
            {
                "kind": "storyboard",
                "storyboard_id": storyboard.storyboard_id,
                "target_duration_seconds": storyboard.target_duration_seconds,
                "selected_ratio": storyboard.selected_ratio,
                "shot_count": len(storyboard.shots),
            },
            {
                "kind": "global_constraints",
                "constraints": storyboard.global_constraints,
            },
            {
                "kind": "reference_assets",
                "reference_asset_ids": storyboard.reference_asset_ids,
                "count": len(storyboard.reference_asset_ids),
            },
            {
                "kind": "storyboard_shots",
                "shots": [
                    {
                        "shot_id": shot.shot_id,
                        "sequence_index": shot.sequence_index,
                        "duration_seconds": shot.duration_seconds,
                        "purpose": shot.purpose,
                        "visual_beat": shot.visual_beat,
                        "dialogue_lines": shot.dialogue_lines,
                        "audio_notes": shot.audio_notes,
                        "constraints": shot.constraints,
                        "reference_asset_ids": shot.reference_asset_ids,
                    }
                    for shot in storyboard.shots
                ],
            },
            {
                "kind": "questions",
                "questions": [
                    "Approve this storyboard, revise the scenes/constraints, or cancel production.",
                    "Provider-specific asset planning and real generation will not start until later approval.",
                ],
            },
        ],
        options=_storyboard_review_options(),
    )


def _storyboard_review_options() -> list[dict[str, str]]:
    return [
        {"decision": "approve", "label": "Approve storyboard"},
        {"decision": "revise", "label": "Revise storyboard"},
        {"decision": "cancel", "label": "Cancel production"},
    ]


def _asset_plan_review_payload(state: ShortVideoProductionState) -> ReviewPayload:
    asset_plan = state.asset_plan
    if asset_plan is None:
        return ReviewPayload(
            review_type="asset_plan_review",
            title="Review short-video asset plan",
            summary=state.brief_summary,
            items=[],
            options=_review_options(),
        )
    questions = [
        "Approve this plan, revise the brief/asset plan, or cancel production."
    ]
    if asset_plan.selected_ratio is None:
        questions.append(
            "Choose one supported aspect ratio before generation: "
            f"{', '.join(asset_plan.ratio_options)}."
        )
    return ReviewPayload(
        review_type="asset_plan_review",
        title=f"Review {_video_type_label(asset_plan.video_type)} asset plan",
        summary=state.brief_summary,
        items=[
            {
                "kind": "video_type",
                "video_type": asset_plan.video_type,
                "label": _video_type_label(asset_plan.video_type),
            },
            {
                "kind": "providers",
                "planned_video_provider": asset_plan.planned_video_provider,
                "provider_label": _provider_label(asset_plan),
                "planned_video_model_name": asset_plan.planned_video_model_name,
                "planned_video_resolution": asset_plan.planned_video_resolution,
                "planned_generate_audio": asset_plan.planned_generate_audio,
                "planned_tts": asset_plan.planned_tts,
                "planned_tts_provider": asset_plan.planned_tts_provider,
                "provider_notes": _provider_notes(asset_plan),
            },
            {
                "kind": "aspect_ratio",
                "options": asset_plan.ratio_options,
                "selected_ratio": asset_plan.selected_ratio,
            },
            {
                "kind": "reference_assets",
                "reference_asset_ids": asset_plan.reference_asset_ids,
                "count": len(asset_plan.reference_asset_ids),
            },
            {
                "kind": "shot_plan",
                "shot_id": asset_plan.shot_plan.shot_id,
                "duration_seconds": asset_plan.shot_plan.duration_seconds,
                "visual_prompt": asset_plan.shot_plan.visual_prompt,
                "voiceover_text": asset_plan.shot_plan.voiceover_text,
            },
            {
                "kind": "shot_asset_plans",
                "count": len(state.shot_asset_plans),
                "segments": [
                    {
                        "shot_asset_plan_id": item.shot_asset_plan_id,
                        "segment_index": item.segment_index,
                        "storyboard_sequence_indexes": item.storyboard_sequence_indexes,
                        "duration_seconds": item.duration_seconds,
                        "status": item.status,
                    }
                    for item in state.shot_asset_plans
                ],
            },
            {"kind": "questions", "questions": questions},
        ],
        options=_review_options(),
    )


def _review_options() -> list[dict[str, str]]:
    return [
        {"decision": "approve", "label": "Approve and generate"},
        {"decision": "revise", "label": "Revise plan"},
        {"decision": "cancel", "label": "Cancel production"},
    ]


def _storyboard_markdown(state: ShortVideoProductionState) -> str:
    """Render a human-readable storyboard projection."""
    storyboard = state.storyboard
    if storyboard is None:
        return "# Short Video Storyboard\n\nNo storyboard has been prepared yet.\n"
    lines = [
        "# Short Video Storyboard",
        "",
        f"- Storyboard ID: `{storyboard.storyboard_id}`",
        f"- Video type: `{storyboard.video_type}`",
        f"- Target duration: {storyboard.target_duration_seconds}s",
        f"- Selected ratio: {storyboard.selected_ratio or 'not selected'}",
        f"- Status: `{storyboard.status}`",
        "",
        "## Summary",
        "",
        storyboard.narrative_summary,
        "",
        "## Global Constraints",
        "",
    ]
    if storyboard.global_constraints:
        lines.extend(f"- {constraint}" for constraint in storyboard.global_constraints)
    else:
        lines.append("- None")
    lines.extend(["", "## Shots", ""])
    for shot in storyboard.shots:
        lines.extend(
            [
                f"### Shot {shot.sequence_index}: {shot.purpose}",
                "",
                f"- Shot ID: `{shot.shot_id}`",
                f"- Duration: {shot.duration_seconds}s",
                f"- Visual beat: {shot.visual_beat}",
                f"- Audio notes: {shot.audio_notes or 'None'}",
                f"- Reference assets: {', '.join(shot.reference_asset_ids) if shot.reference_asset_ids else 'None'}",
                "",
            ]
        )
        if shot.dialogue_lines:
            lines.append("Dialogue:")
            lines.extend(f"- {line}" for line in shot.dialogue_lines)
            lines.append("")
        if shot.constraints:
            lines.append("Constraints:")
            lines.extend(f"- {constraint}" for constraint in shot.constraints)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _planning_duration_seconds(state: ShortVideoProductionState) -> float:
    try:
        value = float(state.planning_context.get("duration_seconds") or 8.0)
    except (TypeError, ValueError):
        return 8.0
    return value if value > 0 else 8.0


def _planning_provider_settings(state: ShortVideoProductionState) -> tuple[str, str, str]:
    provider = _normalize_short_video_provider(state.planning_context.get("video_provider"))
    model_name = _normalize_short_video_model_name(provider, state.planning_context.get("video_model_name"))
    resolution = _normalize_short_video_resolution(
        provider,
        model_name,
        state.planning_context.get("video_resolution"),
    )
    return provider, model_name, resolution


def _planning_video_type(state: ShortVideoProductionState) -> str:
    if state.storyboard is not None:
        return state.storyboard.video_type
    return _normalize_video_type(state.planning_context.get("video_type")) or "product_ad"


def _reference_assets_from_input_files(
    *,
    input_files: Any,
    turn_index: int,
) -> list[ReferenceAssetEntry]:
    references: list[ReferenceAssetEntry] = []
    for file_info in _iter_input_file_records(input_files):
        path = str(file_info.get("path", "") or "").strip()
        if not path:
            continue
        references.append(
            ReferenceAssetEntry(
                reference_asset_id=new_id("reference"),
                path=workspace_relative_path(resolve_workspace_path(path)),
                added_turn_index=turn_index,
                metadata={
                    "name": str(file_info.get("name", "") or "").strip(),
                    "description": str(file_info.get("description", "") or "").strip(),
                },
            )
        )
    return references


def _iter_input_file_records(input_files: Any) -> list[dict[str, Any]]:
    """Return normalized file records from ADK file payload variants."""
    if input_files is None:
        return []
    if isinstance(input_files, (str, dict)):
        candidates = [input_files]
    else:
        try:
            candidates = list(input_files)
        except TypeError:
            return []

    records: list[dict[str, Any]] = []
    for file_info in candidates:
        if isinstance(file_info, str):
            path = file_info.strip()
            if path:
                records.append({"path": path, "name": Path(path).name, "description": ""})
            continue
        if not isinstance(file_info, dict):
            continue
        path = str(file_info.get("path", "") or "").strip()
        if not path:
            continue
        record = dict(file_info)
        record["path"] = path
        record.setdefault("name", Path(path).name)
        record.setdefault("description", "")
        records.append(record)
    return records


def _normalize_render_settings(payload: dict[str, Any]) -> ShortVideoRenderSettings:
    aspect_ratio = str(payload.get("aspect_ratio", "16:9") or "16:9").strip()
    if aspect_ratio not in {"16:9", "9:16", "1:1"}:
        aspect_ratio = "16:9"
    default_dimensions = {
        "16:9": (1280, 720),
        "9:16": (720, 1280),
        "1:1": (1024, 1024),
    }
    default_width, default_height = default_dimensions[aspect_ratio]
    width = _positive_int(payload.get("width"), default_width)
    height = _positive_int(payload.get("height"), default_height)
    fps = _positive_int(payload.get("fps"), 24)
    return ShortVideoRenderSettings(
        aspect_ratio=aspect_ratio,  # type: ignore[arg-type]
        width=width,
        height=height,
        fps=fps,
    )


def _explicit_aspect_ratio(payload: dict[str, Any]) -> str | None:
    aspect_ratio = str(payload.get("aspect_ratio", "") or "").strip()
    return aspect_ratio if aspect_ratio in {"16:9", "9:16", "1:1"} else None


def _resume_selected_ratio(response: dict[str, Any], current_ratio: str | None) -> str | None:
    for key in ("selected_ratio", "aspect_ratio", "ratio"):
        value = str(response.get(key, "") or "").strip()
        if value in {"16:9", "9:16", "1:1"}:
            return value
    return current_ratio if current_ratio in {"16:9", "9:16", "1:1"} else None


def _normalize_resume_decision(response: dict[str, Any]) -> str:
    raw_decision = str(response.get("decision", "") or response.get("action", "") or "").strip().lower()
    if raw_decision in {"approve", "approved", "confirm", "confirmed", "yes", "ok"}:
        return "approve"
    if raw_decision in {"revise", "edit", "change", "modify"}:
        return "revise"
    if raw_decision in {"cancel", "stop", "abort"}:
        return "cancel"
    return raw_decision


def _duration_seconds(payload: dict[str, Any], *, default: float) -> float:
    try:
        value = float(payload.get("duration_seconds", default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _build_placeholder_timeline(
    *,
    asset_id: str,
    audio_id: str,
    render_settings: ShortVideoRenderSettings,
    duration_seconds: float,
) -> ShortVideoTimeline:
    return ShortVideoTimeline(
        timeline_id=new_id("timeline"),
        duration_seconds=duration_seconds,
        render_settings=render_settings,
        video_tracks=[
            VideoTrack(
                track_id="video_main",
                clips=[
                    VideoClip(
                        clip_id="clip_placeholder_video",
                        asset_id=asset_id,
                        start_seconds=0,
                        duration_seconds=duration_seconds,
                    )
                ],
            )
        ],
        audio_tracks=[
            AudioTrack(
                track_id="audio_main",
                kind="silent",
                clips=[
                    AudioClip(
                        clip_id="clip_placeholder_audio",
                        audio_id=audio_id,
                        start_seconds=0,
                        duration_seconds=duration_seconds,
                    )
                ],
            )
        ],
    )


def _build_single_clip_timeline(
    *,
    asset_id: str,
    audio_id: str,
    audio_kind: Literal["voiceover", "bgm", "silent"],
    render_settings: ShortVideoRenderSettings,
    duration_seconds: float,
) -> ShortVideoTimeline:
    return ShortVideoTimeline(
        timeline_id=new_id("timeline"),
        duration_seconds=duration_seconds,
        render_settings=render_settings,
        video_tracks=[
            VideoTrack(
                track_id="video_main",
                clips=[
                    VideoClip(
                        clip_id="clip_provider_video",
                        asset_id=asset_id,
                        start_seconds=0,
                        duration_seconds=duration_seconds,
                    )
                ],
            )
        ],
        audio_tracks=[
            AudioTrack(
                track_id="audio_main",
                kind=audio_kind,
                clips=[
                    AudioClip(
                        clip_id="clip_provider_voiceover",
                        audio_id=audio_id,
                        start_seconds=0,
                        duration_seconds=duration_seconds,
                    )
                ],
            )
        ],
    )


def _first_video_asset_id(state: ShortVideoProductionState) -> str:
    for asset in state.asset_manifest:
        if asset.kind == "video":
            return asset.asset_id
    raise ValueError("Placeholder factory did not create a video asset.")


def _first_audio_id(state: ShortVideoProductionState) -> str:
    for audio in state.audio_manifest:
        return audio.audio_id
    raise ValueError("Placeholder factory did not create an audio asset.")


def _positive_int(value: Any, default: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized > 0 else default


def _status_message(state: ShortVideoProductionState) -> str:
    return (
        f"Short-video production `{state.production_session.production_session_id}` "
        f"is {state.status} at stage `{state.stage}` ({state.progress_percent}%)."
    )
