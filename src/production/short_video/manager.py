"""Runtime service for short-video production."""

from __future__ import annotations

import json
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
from src.production.short_video.placeholders import PlaceholderAssetFactory
from src.production.short_video.providers import (
    ShortVideoProviderError,
    ShortVideoProviderRuntime,
    VeoTtsProviderRuntime,
)
from src.production.short_video.renderer import TimelineRenderer
from src.production.short_video.validators import RenderValidator
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path


_VIEW_TYPES = ("overview", "brief", "asset_plan", "timeline", "events", "artifacts")


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
        self.provider_runtime = provider_runtime or VeoTtsProviderRuntime()
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
        """Start a short-video production run or pause at the first P0b review."""
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

    async def resume(
        self,
        *,
        production_session_id: str | None,
        user_response: dict[str, Any] | None,
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
        response = user_response or {}
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
            state.asset_plan = _build_product_ad_asset_plan(
                user_prompt=state.brief_summary,
                reference_assets=state.reference_assets,
                selected_ratio=current_ratio,
                duration_seconds=duration_seconds,
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
        """Create the P0b product-ad asset plan and pause before provider calls."""
        duration_seconds = _duration_seconds(render_settings_payload, default=8.0)
        state.asset_plan = _build_product_ad_asset_plan(
            user_prompt=user_prompt,
            reference_assets=state.reference_assets,
            selected_ratio=_explicit_aspect_ratio(render_settings_payload),
            duration_seconds=duration_seconds,
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
                message="Prepared a P0b product-ad asset plan and paused before provider generation.",
                metadata={
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
            message="Please review the product-ad asset plan before real video and TTS generation.",
        )

    async def _approve_asset_plan_and_generate(
        self,
        state: ShortVideoProductionState,
        *,
        user_response: dict[str, Any],
        adk_state,
    ) -> ProductionRunResult:
        """Generate media from an approved P0b asset plan."""
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

            state.stage = "tts_generation"
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
                    event_type="voiceover_generated",
                    stage=state.stage,
                    message="Generated one provider-backed voiceover track.",
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
            final_path = session_root / "final" / "final.mp4"
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
                    description="P0b product-ad short-video render.",
                    source=self.capability,
                )
            ]
            state.production_events.append(
                ProductionEvent(
                    event_type="production_completed",
                    stage=state.stage,
                    message="P0b product-ad short-video render completed.",
                    metadata={"artifact_path": state.render_report.output_path},
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_to_adk_state(adk_state, state)
            return self._result_from_state(
                state,
                message="P0b product-ad short-video production completed.",
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


def _build_product_ad_asset_plan(
    *,
    user_prompt: str,
    reference_assets: list[ReferenceAssetEntry],
    selected_ratio: str | None,
    duration_seconds: float,
) -> ShortVideoAssetPlan:
    brief = _brief_summary(user_prompt)
    reference_asset_ids = [item.reference_asset_id for item in reference_assets]
    shot_plan = ShortVideoShotPlan(
        duration_seconds=duration_seconds,
        visual_prompt=_build_product_ad_visual_prompt(brief, reference_assets),
        voiceover_text=_build_voiceover_text(brief),
        reference_asset_ids=reference_asset_ids,
    )
    return ShortVideoAssetPlan(
        selected_ratio=selected_ratio,  # type: ignore[arg-type]
        duration_seconds=duration_seconds,
        reference_asset_ids=reference_asset_ids,
        shot_plan=shot_plan,
    )


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
        "and social-ad pacing."
    )


def _build_voiceover_text(brief: str) -> str:
    cleaned = " ".join(brief.split())
    if len(cleaned) <= 180:
        return cleaned
    return f"{cleaned[:177].rstrip()}..."


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
        title="Review product-ad asset plan",
        summary=state.brief_summary,
        items=[
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
