"""Runtime service for short-video production."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

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
from src.production.session_store import ProductionSessionStore
from src.production.short_video.models import (
    AudioClip,
    AudioTrack,
    ReferenceAssetEntry,
    ShortVideoAssetPlan,
    ShortVideoProductionState,
    ShortVideoRenderSettings,
    ShortVideoShotPlan,
    ShortVideoTimeline,
    VideoClip,
    VideoTrack,
)
from src.production.short_video.impact import build_revision_impact_view
from src.production.short_video.placeholders import PlaceholderAssetFactory
from src.production.short_video.providers import (
    SeedanceNativeAudioProviderRuntime,
    ShortVideoProviderError,
    ShortVideoProviderRuntime,
)
from src.production.short_video.renderer import TimelineRenderer
from src.production.short_video.user_response import normalize_user_response
from src.production.short_video.validators import RenderValidator
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path


_VIEW_TYPES = ("overview", "brief", "asset_plan", "timeline", "events", "artifacts")
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
        self.provider_runtime = provider_runtime or SeedanceNativeAudioProviderRuntime()
        self.renderer = renderer or TimelineRenderer()
        self.validator = validator or RenderValidator()

    async def start(
        self,
        *,
        user_prompt: str,
        input_files: list[dict[str, Any]],
        placeholder_assets: bool,
        render_settings: dict[str, Any] | None,
        adk_state,
    ) -> ProductionRunResult:
        """Start a short-video production run or pause at the first P0 review."""
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
                return self._prepare_asset_plan_review(
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

        target_kinds = _revision_target_kinds(impact_view)
        state.brief_summary = _append_revision_note(state.brief_summary, revision_notes)
        _apply_revision_to_asset_plan(
            state,
            notes=revision_notes,
            target_kinds=target_kinds,
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
        input_files: list[dict[str, Any]],
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
        selected_ratio = state.asset_plan.selected_ratio if state.asset_plan is not None else None
        duration_seconds = state.asset_plan.duration_seconds if state.asset_plan is not None else 8.0
        state.asset_plan = _build_short_video_asset_plan(
            user_prompt=state.brief_summary,
            reference_assets=_valid_reference_assets(state),
            selected_ratio=selected_ratio,
            duration_seconds=duration_seconds,
            video_type=(
                state.asset_plan.video_type
                if state.asset_plan is not None
                else "product_ad"
            ),
        )
        state.status = "needs_user_review"
        state.stage = "asset_plan_review"
        state.progress_percent = max(state.progress_percent, 20)
        state.active_breakpoint = ProductionBreakpoint(
            stage=state.stage,
            review_payload=_asset_plan_review_payload(state),
        )
        state.production_events.append(
            ProductionEvent(
                event_type="reference_assets_added",
                stage=state.stage,
                message="Added reference assets and invalidated dependent short-video outputs.",
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
                "The asset plan is ready for review again."
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
            notes = str(response.get("notes", "") or response.get("message", "") or "").strip()
            if notes:
                state.brief_summary = f"{state.brief_summary}\n\nRevision notes: {notes}"
            current_ratio = state.asset_plan.selected_ratio if state.asset_plan is not None else None
            duration_seconds = state.asset_plan.duration_seconds if state.asset_plan is not None else 8.0
            state.asset_plan = _build_short_video_asset_plan(
                user_prompt=state.brief_summary,
                reference_assets=state.reference_assets,
                selected_ratio=current_ratio,
                duration_seconds=duration_seconds,
                video_type=(
                    state.asset_plan.video_type
                    if state.asset_plan is not None
                    else "product_ad"
                ),
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
                    metadata={"user_response": response},
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_to_adk_state(adk_state, state)
            return self._result_from_state(
                state,
                message="Revision notes were recorded. Please approve, revise again, or cancel the updated plan.",
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

        return await self._approve_asset_plan_and_generate(
            state,
            user_response=response,
            adk_state=adk_state,
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
        state.asset_plan = _build_short_video_asset_plan(
            user_prompt=user_prompt,
            reference_assets=state.reference_assets,
            selected_ratio=_explicit_aspect_ratio(render_settings_payload),
            duration_seconds=duration_seconds,
            video_type=_requested_video_type(user_prompt, render_settings_payload),
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
        """Generate media from an approved P0 short-video asset plan."""
        if state.asset_plan is None:
            return self._fail_state(
                state,
                adk_state=adk_state,
                code="invalid_state",
                message="No asset plan exists for this production session.",
            )

        selected_ratio = _resume_selected_ratio(user_response, state.asset_plan.selected_ratio)
        if selected_ratio is None:
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
                    message="A video aspect ratio must be selected before provider generation.",
                    metadata={"user_response": user_response},
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_to_adk_state(adk_state, state)
            return self._result_from_state(
                state,
                message="Please choose one aspect ratio before approving generation: 9:16, 16:9, or 1:1.",
            )

        state.asset_plan.selected_ratio = selected_ratio
        state.asset_plan.status = "approved"
        state.active_breakpoint = None
        session_root = self.store.session_root(state.production_session)
        settings = _normalize_render_settings({"aspect_ratio": selected_ratio})
        duration_seconds = state.asset_plan.duration_seconds

        try:
            _mark_existing_generated_media_superseded(
                state,
                reason="New approved generation supersedes previous generated outputs.",
            )
            state.status = "running"
            state.stage = "provider_generation"
            state.progress_percent = 45
            video_asset = await self.provider_runtime.generate_video_clip(
                session_root=session_root,
                asset_plan=state.asset_plan,
                render_settings=settings,
                reference_assets=state.reference_assets,
                owner_ref=state.production_session.owner_ref,
            )
            state.asset_manifest.append(video_asset)
            state.production_events.append(
                ProductionEvent(
                    event_type="video_clip_generated",
                    stage=state.stage,
                    message="Generated one provider-backed video clip.",
                    metadata={"asset_id": video_asset.asset_id, "provider": video_asset.provider},
                )
            )

            state.stage = "audio_generation"
            state.progress_percent = 60
            audio_asset = await self.provider_runtime.synthesize_voiceover(
                session_root=session_root,
                asset_plan=state.asset_plan,
                render_settings=settings,
                owner_ref=state.production_session.owner_ref,
            )
            state.audio_manifest.append(audio_asset)
            state.production_events.append(
                ProductionEvent(
                    event_type="audio_track_generated",
                    stage=state.stage,
                    message="Generated one provider-backed audio track.",
                    metadata={"audio_id": audio_asset.audio_id, "provider": audio_asset.provider},
                )
            )

            state.timeline = _build_single_clip_timeline(
                asset_id=video_asset.asset_id,
                audio_id=audio_asset.audio_id,
                audio_kind="voiceover",
                render_settings=settings,
                duration_seconds=duration_seconds,
            )
            final_path = _final_output_path(session_root, state.asset_plan.plan_id)
            state.stage = "rendering"
            state.progress_percent = 75
            state.render_report = self.renderer.render(
                timeline=state.timeline,
                asset_manifest=state.asset_manifest,
                audio_manifest=state.audio_manifest,
                output_path=final_path,
            )
            state.stage = "validation"
            state.progress_percent = 90
            state.render_validation_report = self.validator.validate(state.render_report.output_path)
            if state.render_validation_report.status != "valid":
                raise RuntimeError("; ".join(state.render_validation_report.issues) or "render validation failed")

            state.status = "completed"
            state.stage = "completed"
            state.progress_percent = 100
            state.artifacts = [
                WorkspaceFileRef(
                    name="final.mp4",
                    path=state.render_report.output_path,
                    description=f"P0 {_video_type_label(state.asset_plan.video_type)} short-video render.",
                    source=self.capability,
                )
            ]
            state.production_events.append(
                ProductionEvent(
                    event_type="production_completed",
                    stage=state.stage,
                    message=f"P0 {_video_type_label(state.asset_plan.video_type)} short-video render completed.",
                    metadata={"artifact_path": state.render_report.output_path},
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_to_adk_state(adk_state, state)
            return self._result_from_state(
                state,
                message=f"P0 {_video_type_label(state.asset_plan.video_type)} short-video production completed.",
            )
        except ShortVideoProviderError as exc:
            return self._fail_state(
                state,
                adk_state=adk_state,
                code="provider_failed",
                message=str(exc),
                provider=state.asset_plan.planned_video_provider,
            )
        except Exception as exc:
            return self._fail_state(
                state,
                adk_state=adk_state,
                code="short_video_p0b_failed",
                message=f"{type(exc).__name__}: {exc}",
            )

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


def _resolve_requested_session_id(production_session_id: str | None, adk_state) -> str:
    requested = str(production_session_id or "").strip()
    if requested:
        return requested
    return str(adk_state.get("active_production_session_id", "") or "").strip()


def _normalize_view_type(view_type: str | None) -> str | None:
    value = str(view_type or "overview").strip().lower() or "overview"
    return value if value in _VIEW_TYPES else None


def _build_production_view(state: ShortVideoProductionState, view_type: str) -> dict[str, Any]:
    if view_type == "overview":
        return _overview_view(state)
    if view_type == "brief":
        return _brief_view(state)
    if view_type == "asset_plan":
        return _asset_plan_view(state)
    if view_type == "timeline":
        return _timeline_view(state)
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
                "asset_manifest": len(state.asset_manifest),
                "audio_manifest": len(state.audio_manifest),
                "artifacts": len(state.artifacts),
                "events": len(state.production_events),
            },
            "has_timeline": state.timeline is not None,
            "has_render_report": state.render_report is not None,
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


def _asset_plan_view(state: ShortVideoProductionState) -> dict[str, Any]:
    view = _base_view(state, "asset_plan")
    view.update(
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


def _artifacts_view(state: ShortVideoProductionState) -> dict[str, Any]:
    view = _base_view(state, "artifacts")
    view.update(
        {
            "artifacts": [item.model_dump(mode="json") for item in state.artifacts],
            "asset_manifest": [item.model_dump(mode="json") for item in state.asset_manifest],
            "audio_manifest": [item.model_dump(mode="json") for item in state.audio_manifest],
            "final_dir": f"{state.production_session.root_dir}/final",
        }
    )
    return view


def _brief_summary(user_prompt: str) -> str:
    prompt = str(user_prompt or "").strip()
    return prompt or "Short-video production."


def _build_short_video_asset_plan(
    *,
    user_prompt: str,
    reference_assets: list[ReferenceAssetEntry],
    selected_ratio: str | None,
    duration_seconds: float,
    video_type: str,
) -> ShortVideoAssetPlan:
    brief = _brief_summary(user_prompt)
    normalized_video_type = _normalize_video_type(video_type) or "product_ad"
    reference_asset_ids = [item.reference_asset_id for item in reference_assets]
    shot_plan = ShortVideoShotPlan(
        duration_seconds=duration_seconds,
        visual_prompt=_build_visual_prompt(normalized_video_type, brief, reference_assets),
        voiceover_text=_build_voiceover_text(brief),
        reference_asset_ids=reference_asset_ids,
    )
    return ShortVideoAssetPlan(
        video_type=normalized_video_type,  # type: ignore[arg-type]
        selected_ratio=selected_ratio,  # type: ignore[arg-type]
        duration_seconds=duration_seconds,
        reference_asset_ids=reference_asset_ids,
        shot_plan=shot_plan,
    )


def _build_visual_prompt(
    video_type: str,
    brief: str,
    reference_assets: list[ReferenceAssetEntry],
) -> str:
    if video_type == "cartoon_short_drama":
        return _build_cartoon_short_drama_visual_prompt(brief, reference_assets)
    if video_type == "social_media_short":
        return _build_social_media_visual_prompt(brief, reference_assets)
    return _build_product_ad_visual_prompt(brief, reference_assets)


def _build_product_ad_visual_prompt(
    brief: str,
    reference_assets: list[ReferenceAssetEntry],
) -> str:
    reference_note = (
        "Use the provided product reference assets as identity anchors."
        if reference_assets
        else "No product reference image is available; infer visual identity from the brief."
    )
    return (
        "Create a concise product advertising short video. "
        f"Brief: {brief}. "
        f"{reference_note} "
        "Keep the product clear, readable, and central. Use polished lighting, simple motion, "
        f"and social-ad pacing. {_build_native_audio_instruction(brief)}"
    )


def _build_cartoon_short_drama_visual_prompt(
    brief: str,
    reference_assets: list[ReferenceAssetEntry],
) -> str:
    reference_note = (
        "Use the provided reference assets as character, product, or style anchors."
        if reference_assets
        else "Infer character and style identity from the brief."
    )
    return (
        "Create a concise single-shot cartoon short-drama video for P0 validation. "
        f"Brief: {brief}. "
        f"{reference_note} "
        "Emphasize clear character action, readable emotion, light comedic timing, "
        f"and a simple visual beat that can later expand into multi-shot storyboards. "
        f"{_build_native_audio_instruction(brief)}"
    )


def _build_social_media_visual_prompt(
    brief: str,
    reference_assets: list[ReferenceAssetEntry],
) -> str:
    reference_note = (
        "Use the provided reference assets as identity or style anchors."
        if reference_assets
        else "Infer the visual identity from the brief."
    )
    return (
        "Create a concise single-shot social media short video for P0 validation. "
        f"Brief: {brief}. "
        f"{reference_note} "
        "Use a strong opening visual hook, fast readable motion, platform-friendly framing, "
        f"and clear subject focus suitable for short-form feeds. {_build_native_audio_instruction(brief)}"
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
        return (
            "Native audio instructions: generate synchronized character voices, sound effects, "
            "and timing directly in the video. Spoken dialogue to generate exactly, with no "
            f"narrator reading the task description: {'; '.join(dialogue_lines)}. "
            f"{subtitle_note} Honor any requested voice style in the brief."
        )
    return (
        "Native audio instructions: generate synchronized audio that matches the scene, "
        "including voices, sound effects, and music if requested. Do not read the task "
        f"description as narration unless the brief explicitly asks for narration. {subtitle_note}"
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


def _requested_video_type(user_prompt: str, payload: dict[str, Any]) -> str:
    for key in ("video_type", "short_video_type", "production_type", "project_type"):
        normalized = _normalize_video_type(payload.get(key))
        if normalized:
            return normalized
    return _infer_video_type_from_text(user_prompt)


def _infer_video_type_from_text(text: str) -> str:
    normalized = str(text or "").strip().lower()
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


def _build_voiceover_text(brief: str) -> str:
    cleaned = " ".join(brief.split())
    if len(cleaned) <= 180:
        return cleaned
    return f"{cleaned[:177].rstrip()}..."


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
) -> None:
    asset_plan = state.asset_plan
    if asset_plan is None:
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
    )


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

    if "video_asset" in impacted_kinds:
        for asset in state.asset_manifest:
            if asset.kind == "video" and asset.status == "valid":
                asset.status = "stale"
                asset.stale_reason = reason
    if "audio_asset" in impacted_kinds:
        for audio in state.audio_manifest:
            if audio.status == "valid":
                audio.status = "stale"
                audio.stale_reason = reason
    if impacted_kinds & {"timeline", "video_asset", "audio_asset"}:
        state.timeline = None
        state.render_report = None
        state.render_validation_report = None
    if "final_artifact" in impacted_kinds:
        for artifact in state.artifacts:
            if "stale" not in artifact.description.lower():
                artifact.description = (
                    f"{artifact.description} May be stale after revision."
                ).strip()


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


def _final_output_path(session_root: Path, plan_id: str) -> Path:
    """Return a version-safe final MP4 path for one approved asset plan."""
    plan_segment = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in plan_id
    )
    return session_root / "final" / (plan_segment or new_id("asset_plan")) / "final.mp4"


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
        questions.append("Choose one aspect ratio before generation: 9:16, 16:9, or 1:1.")
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
                "planned_tts": asset_plan.planned_tts,
                "planned_tts_provider": asset_plan.planned_tts_provider,
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


def _reference_assets_from_input_files(
    *,
    input_files: list[dict[str, Any]],
    turn_index: int,
) -> list[ReferenceAssetEntry]:
    references: list[ReferenceAssetEntry] = []
    for file_info in input_files:
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
