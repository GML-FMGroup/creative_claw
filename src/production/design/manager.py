"""Runtime service for Design production."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.production.design.artifact_lineage import (
    artifact_lineage_json,
    artifact_lineage_markdown,
    build_artifact_lineage,
)
from src.production.design.accessibility import (
    accessibility_report_json,
    accessibility_report_markdown,
    build_accessibility_report,
)
from src.production.design.browser_diagnostics import (
    browser_diagnostics_markdown,
    build_browser_diagnostics,
)
from src.production.design.impact import build_revision_impact_view, normalize_revision_request
from src.production.design.models import (
    AccessibilityReport,
    ArtifactLineageReport,
    BrowserDiagnosticsReport,
    ComponentInventoryReport,
    DesignBrief,
    DesignQcFinding,
    DesignQcReport,
    DesignBuildMode,
    DesignProductionState,
    DesignSystemAuditReport,
    DesignSystemExtractionReport,
    DesignSystemSpec,
    DesignTokenColor,
    DesignTokenTypography,
    HtmlArtifact,
    HtmlValidationReport,
    LayoutPlan,
    LayoutSection,
    PageBlueprint,
    PageHandoffReport,
    PdfExportReport,
    PreviewReport,
    ViewportSpec,
)
from src.production.design.component_inventory import (
    build_component_inventory,
    component_inventory_json,
    component_inventory_markdown,
)
from src.production.design.design_system_audit import audit_design_system, design_system_audit_markdown
from src.production.design.design_system_extractor import (
    build_design_system_extraction,
    design_system_extraction_json,
    design_system_extraction_markdown,
)
from src.production.design.expert_runtime import DesignExpertRuntime
from src.production.design.handoff import write_handoff_exports
from src.production.design.page_handoff import (
    build_page_handoff,
    page_handoff_json,
    page_handoff_markdown,
)
from src.production.design.placeholders import PlaceholderHtmlBuilder
from src.production.design.quality import build_quality_report, quality_report_markdown
from src.production.design.source_refs import (
    latest_html_artifact,
    preview_report_source_refs,
    source_ref_details,
    workspace_file_source_refs,
)
from src.production.design.tools.asset_ingestor import AssetIngestor
from src.production.design.tools.html_validator import HtmlValidator
from src.production.design.tools.pdf_exporter import HtmlPdfExporter
from src.production.design.tools.preview_renderer import HtmlPreviewRenderer
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
from src.production.projection import get_active_production_session_id
from src.production.session_store import ProductionSessionStore
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path


_VIEW_TYPES = (
    "overview",
    "brief",
    "design_system",
    "design_system_extraction",
    "components",
    "accessibility",
    "layout",
    "preview",
    "diagnostics",
    "lineage",
    "pages",
    "quality",
    "events",
    "artifacts",
)
_DEFAULT_PREVIEW_DEVICE_TARGETS = ("desktop", "mobile")
_PREVIEW_VIEWPORT_BY_DEVICE = {
    "desktop": ViewportSpec(name="desktop", width=1440, height=1000),
    "tablet": ViewportSpec(name="tablet", width=820, height=1180),
    "mobile": ViewportSpec(name="mobile", width=390, height=844),
}


class DesignProductionManager:
    """Coordinate Design production state, deterministic tools, and projection."""

    capability = "design"

    def __init__(
        self,
        *,
        store: ProductionSessionStore | None = None,
        asset_ingestor: AssetIngestor | None = None,
        html_validator: HtmlValidator | None = None,
        preview_renderer: HtmlPreviewRenderer | None = None,
        pdf_exporter: HtmlPdfExporter | None = None,
        placeholder_builder: PlaceholderHtmlBuilder | None = None,
        expert_runtime: DesignExpertRuntime | None = None,
    ) -> None:
        """Initialize the Design production manager."""
        self.store = store or ProductionSessionStore()
        self.asset_ingestor = asset_ingestor or AssetIngestor()
        self.html_validator = html_validator or HtmlValidator()
        self.preview_renderer = preview_renderer or HtmlPreviewRenderer()
        self.pdf_exporter = pdf_exporter or HtmlPdfExporter()
        self.placeholder_builder = placeholder_builder or PlaceholderHtmlBuilder()
        self.expert_runtime = expert_runtime or DesignExpertRuntime()

    async def start(
        self,
        *,
        user_prompt: str,
        input_files: list[dict[str, Any]],
        placeholder_design: bool,
        design_settings: dict[str, Any] | None,
        adk_state,
    ) -> ProductionRunResult:
        """Start a Design production run using the P0a or gated P0b skeleton path."""
        context = _context_from_adk_state(adk_state)
        production_session = self.store.create_session(
            capability=self.capability,
            adk_session_id=context["sid"],
            turn_index=context["turn_index"],
            owner_ref=context["owner_ref"],
        )
        session_root = self.store.session_root(production_session)
        _ensure_design_dirs(session_root)
        genre = _infer_design_genre(user_prompt, design_settings or {})
        state = DesignProductionState(
            production_session=production_session,
            status="running",
            stage="initializing",
            progress_percent=5,
            design_genre=genre,
            build_mode=_build_mode_from_settings(design_settings or {}),
            requested_exports=_requested_exports_from_settings(design_settings or {}),
        )
        state.production_events.append(
            ProductionEvent(
                event_type="production_started",
                stage=state.stage,
                message="Design production session started.",
            )
        )
        try:
            state.reference_assets = self.asset_ingestor.ingest(
                session_root=session_root,
                input_files=input_files,
                turn_index=context["turn_index"],
            )
            if placeholder_design:
                _prepare_placeholder_planning_state(state, user_prompt=user_prompt, design_settings=design_settings or {})
                return await self._build_and_complete_placeholder(state, adk_state=adk_state)

            await self._prepare_expert_direction_state(
                state,
                user_prompt=user_prompt,
                design_settings=design_settings or {},
            )
            if state.brief is not None:
                state.brief.confirmed = False
            if state.layout_plan is not None:
                for page in state.layout_plan.pages:
                    page.status = "draft"
            if state.design_system is not None:
                state.design_system.source = "generated" if state.design_system.source == "placeholder" else state.design_system.source
            _append_design_system_audit(state)
            return self._pause_for_design_direction_review(
                state,
                message="Design direction is ready for review before HTML generation.",
                adk_state=adk_state,
            )
        except Exception as exc:
            state.status = "failed"
            state.stage = "failed"
            state.progress_percent = max(state.progress_percent, 5)
            state.production_events.append(
                ProductionEvent(
                    event_type="production_failed",
                    stage=state.stage,
                    message=f"Design production failed: {type(exc).__name__}: {exc}",
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            return self._result_from_state(
                state,
                message="Design production failed.",
                error=ProductionErrorInfo(
                    code="design_start_failed",
                    message=f"{type(exc).__name__}: {exc}",
                ),
            )

    async def status(
        self,
        *,
        production_session_id: str | None,
        adk_state,
    ) -> ProductionRunResult:
        """Return a read-only status snapshot for a Design production run."""
        loaded = self._load_state_or_error(production_session_id, adk_state)
        if isinstance(loaded, ProductionRunResult):
            return loaded
        return self._result_from_state(loaded, message=_status_message(loaded))

    async def view(
        self,
        *,
        production_session_id: str | None,
        view_type: str | None,
        adk_state,
    ) -> ProductionRunResult:
        """Return a read-only Design production view derived from persisted state."""
        loaded = self._load_state_or_error(production_session_id, adk_state)
        if isinstance(loaded, ProductionRunResult):
            return loaded
        normalized_view_type = _normalize_view_type(view_type)
        if normalized_view_type is None:
            return ProductionRunResult(
                status="failed",
                capability=self.capability,
                production_session_id=loaded.production_session.production_session_id,
                stage="invalid_view_type",
                progress_percent=loaded.progress_percent,
                message=f"Unsupported design production view_type. Allowed: {', '.join(_VIEW_TYPES)}.",
                error=ProductionErrorInfo(
                    code="invalid_view_type",
                    message=f"Unsupported design production view_type. Allowed: {', '.join(_VIEW_TYPES)}.",
                ),
            )
        return self._result_from_state(
            loaded,
            message=f"Loaded design production view: {normalized_view_type}.",
            view=_build_production_view(loaded, normalized_view_type),
        )

    async def resume(
        self,
        *,
        production_session_id: str | None,
        user_response: Any | None,
        adk_state,
    ) -> ProductionRunResult:
        """Resume a Design production session from an active review breakpoint."""
        loaded = self._load_state_or_error(production_session_id, adk_state)
        if isinstance(loaded, ProductionRunResult):
            return loaded
        state = loaded
        response = _normalize_user_response(user_response)
        decision = _normalize_resume_decision(response)
        if state.active_breakpoint is None:
            return self._result_from_state(state, message="There is no active design review breakpoint to resume.")
        if decision == "cancel":
            state.status = "cancelled"
            state.stage = "cancelled"
            state.active_breakpoint = None
            state.production_events.append(
                ProductionEvent(
                    event_type="production_cancelled",
                    stage=state.stage,
                    message="User cancelled Design production.",
                    metadata={"user_response": response},
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_to_adk_state(adk_state, state)
            return self._result_from_state(state, message="Design production was cancelled.")
        if decision == "revise":
            if state.active_breakpoint.stage == "preview_review":
                return await self._apply_revision_and_rebuild(
                    state,
                    revision_request=normalize_revision_request(response),
                    adk_state=adk_state,
                )
            return self._capture_direction_revision(state, response=response, adk_state=adk_state)
        if decision != "approve":
            return self._result_from_state(state, message="Please respond with decision=approve, revise, or cancel.")

        if state.active_breakpoint.stage == "design_direction_review":
            return await self._build_and_pause_for_preview_review(state, adk_state=adk_state)
        if state.active_breakpoint.stage == "preview_review":
            return await self._approve_preview_and_complete(state, response=response, adk_state=adk_state)
        return self._result_from_state(
            state,
            message=f"Current design review stage cannot be approved: {state.active_breakpoint.stage}.",
        )

    async def add_reference_assets(
        self,
        *,
        production_session_id: str | None,
        input_files: list[dict[str, Any]],
        user_response: Any | None,
        adk_state,
    ) -> ProductionRunResult:
        """Add reference assets to an existing Design production session."""
        loaded = self._load_state_or_error(production_session_id, adk_state)
        if isinstance(loaded, ProductionRunResult):
            return loaded
        state = loaded
        context = _context_from_adk_state(adk_state)
        session_root = self.store.session_root(state.production_session)
        new_assets = self.asset_ingestor.ingest(
            session_root=session_root,
            input_files=input_files,
            turn_index=context["turn_index"],
        )
        state.reference_assets.extend(new_assets)
        for artifact in state.html_artifacts:
            artifact.status = "stale"
            artifact.stale_reason = "Reference assets changed."
        state.artifacts = []
        state.export_artifacts = []
        state.production_events.append(
            ProductionEvent(
                event_type="reference_assets_added",
                stage=state.stage,
                message=f"Added {len(new_assets)} reference asset(s) to Design production.",
                metadata={"user_response": _normalize_user_response(user_response)},
            )
        )
        return self._pause_for_design_direction_review(
            state,
            message="Reference assets were added. Review the direction before rebuilding HTML.",
            adk_state=adk_state,
        )

    async def analyze_revision_impact(
        self,
        *,
        production_session_id: str | None,
        user_response: Any | None,
        adk_state,
    ) -> ProductionRunResult:
        """Return a read-only impact analysis for a requested Design revision."""
        loaded = self._load_state_or_error(production_session_id, adk_state)
        if isinstance(loaded, ProductionRunResult):
            return loaded
        return self._result_from_state(
            loaded,
            message="Loaded Design revision impact analysis.",
            view=build_revision_impact_view(loaded, user_response),
        )

    async def apply_revision(
        self,
        *,
        production_session_id: str | None,
        user_response: Any | None,
        adk_state,
    ) -> ProductionRunResult:
        """Apply a confirmed P0 Design revision and return to review."""
        loaded = self._load_state_or_error(production_session_id, adk_state)
        if isinstance(loaded, ProductionRunResult):
            return loaded
        state = loaded
        revision_request = normalize_revision_request(user_response)
        if state.html_artifacts:
            return await self._apply_revision_and_rebuild(
                state,
                revision_request=revision_request,
                adk_state=adk_state,
            )
        impact_view = build_revision_impact_view(state, revision_request)
        state.revision_history.append(_revision_history_entry(revision_request, impact_view=impact_view))
        state.production_events.append(
            ProductionEvent(
                event_type="design_direction_revised",
                stage=state.stage,
                message="Revision was captured before HTML generation; returning to design direction review.",
                metadata={"user_response": revision_request},
            )
        )
        return self._pause_for_design_direction_review(
            state,
            message="Revision captured. Review the updated direction before building the page.",
            adk_state=adk_state,
            view=impact_view,
        )

    def _load_state_or_error(self, production_session_id: str | None, adk_state) -> DesignProductionState | ProductionRunResult:
        context = _context_from_adk_state(adk_state)
        session_id = _resolve_requested_session_id(production_session_id, adk_state)
        try:
            return self.store.load_state(
                production_session_id=session_id,
                adk_session_id=context["sid"],
                owner_ref=context["owner_ref"],
                state_type=DesignProductionState,
                capability=self.capability,
            )
        except ProductionSessionNotFoundError:
            return ProductionRunResult(
                status="failed",
                capability=self.capability,
                production_session_id=session_id or "",
                stage="not_found",
                progress_percent=0,
                message="Design production session was not found or is not owned by this conversation.",
                error=ProductionErrorInfo(
                    code="production_session_not_found_or_not_owned",
                    message="Design production session was not found or is not owned by this conversation.",
                ),
            )
        except ProductionRuntimeError as exc:
            return ProductionRunResult(
                status="failed",
                capability=self.capability,
                production_session_id=session_id or "",
                stage="load_failed",
                progress_percent=0,
                message="Design production session could not be loaded.",
                error=ProductionErrorInfo(code="production_state_load_failed", message=str(exc)),
            )

    def _pause_for_design_direction_review(
        self,
        state: DesignProductionState,
        *,
        message: str,
        adk_state,
        view: dict[str, Any] | None = None,
    ) -> ProductionRunResult:
        state.status = "needs_user_review"
        state.stage = "design_direction_review"
        state.progress_percent = max(state.progress_percent, 35)
        state.active_breakpoint = ProductionBreakpoint(
            stage=state.stage,
            review_payload=_design_direction_review_payload(state),
        )
        state.production_events.append(
            ProductionEvent(
                event_type="design_direction_review_ready",
                stage=state.stage,
                message=message,
            )
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_pointer_to_adk_state(adk_state, state)
        return self._result_from_state(state, message=message, view=view)

    def _capture_direction_revision(
        self,
        state: DesignProductionState,
        *,
        response: dict[str, Any],
        adk_state,
    ) -> ProductionRunResult:
        """Capture direction-level revision notes before HTML generation."""
        revision_request = normalize_revision_request(response)
        impact_view = build_revision_impact_view(state, revision_request)
        state.revision_history.append(_revision_history_entry(revision_request, impact_view=impact_view))
        state.production_events.append(
            ProductionEvent(
                event_type="design_direction_revised",
                stage=state.stage,
                message="User requested design revisions; returning to design direction review.",
                metadata={"user_response": revision_request},
            )
        )
        return self._pause_for_design_direction_review(
            state,
            message="Design revision notes were captured. Review the updated direction before rebuilding.",
            adk_state=adk_state,
            view=impact_view,
        )

    async def _build_and_complete_placeholder(self, state: DesignProductionState, *, adk_state) -> ProductionRunResult:
        await self._build_html_validation_preview_and_qc(state, builder_mode="placeholder")
        await self._export_requested_pdf(state, response={})
        _append_browser_diagnostics(state, latest_html_artifact(state))
        _append_artifact_lineage(state)
        state.status = "completed"
        state.stage = "completed"
        state.progress_percent = 100
        state.active_breakpoint = None
        state.production_events.append(
            ProductionEvent(
                event_type="production_completed",
                stage=state.stage,
                message="P0a placeholder Design production completed.",
            )
        )
        self._finalize_artifacts(state)
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_to_adk_state(adk_state, state)
        return self._result_from_state(state, message="P0a placeholder Design production completed.")

    async def _build_and_pause_for_preview_review(self, state: DesignProductionState, *, adk_state) -> ProductionRunResult:
        if state.brief is not None:
            state.brief.confirmed = True
        if state.layout_plan is not None:
            for page in state.layout_plan.pages:
                page.status = "approved"
        try:
            await self._build_html_validation_preview_and_qc(state, builder_mode="expert")
        except Exception as exc:
            state.status = "failed"
            state.stage = "failed"
            state.production_events.append(
                ProductionEvent(
                    event_type="html_build_failed",
                    stage=state.stage,
                    message=f"Design HTML build failed: {type(exc).__name__}: {exc}",
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_pointer_to_adk_state(adk_state, state)
            return self._result_from_state(
                state,
                message="Design HTML build failed.",
                error=ProductionErrorInfo(
                    code="design_html_build_failed",
                    message=f"{type(exc).__name__}: {exc}",
                ),
            )
        state.status = "needs_user_review"
        state.stage = "preview_review"
        state.progress_percent = 85
        state.active_breakpoint = ProductionBreakpoint(
            stage=state.stage,
            review_payload=_preview_review_payload(state),
        )
        state.production_events.append(
            ProductionEvent(
                event_type="preview_review_ready",
                stage=state.stage,
                message="Generated HTML design is ready for review.",
            )
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_pointer_to_adk_state(adk_state, state)
        return self._result_from_state(state, message="Generated HTML design is ready for review.")

    async def _apply_revision_and_rebuild(
        self,
        state: DesignProductionState,
        *,
        revision_request: dict[str, Any],
        adk_state,
    ) -> ProductionRunResult:
        """Apply a preview-stage revision by rebuilding affected HTML page artifacts."""
        impact_view = build_revision_impact_view(state, revision_request)
        revision_request_for_build = dict(revision_request)
        revision_request_for_build["revision_id"] = impact_view.get("revision_id", "")
        revision_entry = _revision_history_entry(revision_request_for_build, impact_view=impact_view)
        state.revision_history.append(revision_entry)
        stale_reason = _revision_stale_reason(revision_entry)
        affected_page_ids = set(_revision_page_ids(state, impact_view))
        for artifact in state.html_artifacts:
            if artifact.status != "failed" and artifact.page_id in affected_page_ids:
                artifact.status = "stale"
                artifact.stale_reason = stale_reason
        state.artifacts = []
        state.export_artifacts = []
        state.status = "running"
        state.stage = "revision_applying"
        state.progress_percent = max(state.progress_percent, 70)
        state.active_breakpoint = None
        state.production_events.append(
            ProductionEvent(
                event_type="revision_applied",
                stage=state.stage,
                message="Design revision was applied; rebuilding affected HTML page artifacts.",
                metadata={"impact": impact_view, "affected_page_ids": sorted(affected_page_ids)},
            )
        )
        try:
            await self._build_html_validation_preview_and_qc(
                state,
                builder_mode="revision",
                revision_request=revision_request_for_build,
                revision_impact=impact_view,
            )
        except Exception as exc:
            state.status = "failed"
            state.stage = "failed"
            state.active_breakpoint = None
            state.production_events.append(
                ProductionEvent(
                    event_type="html_revision_failed",
                    stage=state.stage,
                    message=f"Design revision build failed: {type(exc).__name__}: {exc}",
                )
            )
            self._save_projection_files(state)
            self.store.save_state(state)
            self.store.project_pointer_to_adk_state(adk_state, state)
            return self._result_from_state(
                state,
                message="Design revision build failed.",
                view=impact_view,
                error=ProductionErrorInfo(
                    code="design_html_revision_failed",
                    message=f"{type(exc).__name__}: {exc}",
                ),
            )
        state.status = "needs_user_review"
        state.stage = "preview_review"
        state.progress_percent = 85
        state.active_breakpoint = ProductionBreakpoint(
            stage=state.stage,
            review_payload=_preview_review_payload(state),
        )
        state.production_events.append(
            ProductionEvent(
                event_type="revision_preview_review_ready",
                stage=state.stage,
                message="Rebuilt HTML design is ready for review.",
                metadata={"revision_id": revision_entry.get("revision_id", "")},
            )
        )
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_pointer_to_adk_state(adk_state, state)
        return self._result_from_state(
            state,
            message="Rebuilt HTML design is ready for review.",
            view=impact_view,
        )

    async def _approve_preview_and_complete(
        self,
        state: DesignProductionState,
        *,
        response: dict[str, Any],
        adk_state,
    ) -> ProductionRunResult:
        for artifact in state.html_artifacts:
            if artifact.status in {"draft", "valid"}:
                artifact.status = "approved"
        await self._export_requested_pdf(state, response=response)
        _append_browser_diagnostics(state, latest_html_artifact(state))
        _append_artifact_lineage(state)
        state.status = "completed"
        state.stage = "completed"
        state.progress_percent = 100
        state.active_breakpoint = None
        state.production_events.append(
            ProductionEvent(
                event_type="production_completed",
                stage=state.stage,
                message="User approved generated HTML design.",
            )
        )
        self._finalize_artifacts(state)
        self._save_projection_files(state)
        self.store.save_state(state)
        self.store.project_to_adk_state(adk_state, state)
        return self._result_from_state(state, message="Design production completed.")

    async def _prepare_expert_direction_state(
        self,
        state: DesignProductionState,
        *,
        user_prompt: str,
        design_settings: dict[str, Any],
    ) -> None:
        """Prepare brief, design system, and layout with internal structured experts."""
        state.stage = "brief_preparing"
        state.progress_percent = max(state.progress_percent, 12)
        direction = await self.expert_runtime.plan_direction(
            user_prompt=user_prompt,
            design_genre=state.design_genre or _infer_design_genre(user_prompt, design_settings),
            design_settings=design_settings,
            reference_assets=state.reference_assets,
        )
        if not direction.layout_plan.pages:
            raise ValueError("LayoutPlannerExpert returned no pages.")
        for page in direction.layout_plan.pages:
            page.path = _normalize_page_path(page.path)
        state.brief = direction.brief
        state.design_genre = direction.brief.design_genre
        state.design_system = direction.design_system
        state.layout_plan = direction.layout_plan
        state.stage = "layout_prepared"
        state.progress_percent = max(state.progress_percent, 35)
        state.production_events.append(
            ProductionEvent(
                event_type="expert_direction_prepared",
                stage=state.stage,
                message="Prepared Design brief, design system, and layout plan with internal structured experts.",
                metadata={
                    "model_name": getattr(self.expert_runtime, "model_name", ""),
                    "notes": direction.notes,
                },
            )
        )

    async def _build_html_validation_preview_and_qc(
        self,
        state: DesignProductionState,
        *,
        builder_mode: str,
        revision_request: dict[str, Any] | None = None,
        revision_impact: dict[str, Any] | None = None,
    ) -> None:
        session_root = self.store.session_root(state.production_session)
        _ensure_design_dirs(session_root)
        state.stage = "html_building"
        state.progress_percent = max(state.progress_percent, 55)
        target_pages = _target_pages_for_html_build(
            state,
            builder_mode=builder_mode,
            revision_impact=revision_impact,
        )
        built_artifacts: list[HtmlArtifact] = []
        last_processing: dict[str, Any] = {}
        for page in target_pages:
            state.stage = "html_building"
            shared_html_context = (
                _shared_html_context_for_artifacts(built_artifacts)
                if state.build_mode == "multi_html" and builder_mode in {"expert", "revision"}
                else ""
            )
            artifact = await self._build_html_artifact_for_page(
                session_root=session_root,
                state=state,
                page=page,
                builder_mode=builder_mode,
                revision_request=revision_request,
                revision_impact=revision_impact,
                shared_html_context=shared_html_context,
            )
            validation_report = self.html_validator.validate(
                artifact.path,
                session_root=session_root,
                artifact_id=artifact.artifact_id,
            )
            if validation_report.status == "invalid" and builder_mode in {"expert", "revision"}:
                repair_source_artifact_id = artifact.artifact_id
                repair_feedback = _html_validation_repair_feedback(validation_report)
                state.production_events.append(
                    ProductionEvent(
                        event_type="html_validation_repair_attempted",
                        stage=state.stage,
                        message="Generated HTML failed validation; requesting one HtmlBuilderExpert repair pass.",
                        metadata={
                            "artifact_id": repair_source_artifact_id,
                            "page_id": page.page_id,
                            "issues": validation_report.issues,
                        },
                    )
                )
                artifact = await self._build_html_artifact_for_page(
                    session_root=session_root,
                    state=state,
                    page=page,
                    builder_mode=builder_mode,
                    revision_request=revision_request,
                    revision_impact=revision_impact,
                    validation_feedback=repair_feedback,
                    previous_html_override=_read_artifact_html(artifact),
                    shared_html_context=shared_html_context,
                )
                artifact.metadata["validation_repair_attempted"] = True
                artifact.metadata["validation_repair_source_artifact_id"] = repair_source_artifact_id
                validation_report = self.html_validator.validate(
                    artifact.path,
                    session_root=session_root,
                    artifact_id=artifact.artifact_id,
                )
            state.html_artifacts.append(artifact)
            built_artifacts.append(artifact)
            last_processing = await self._validate_preview_and_qc_artifact(
                session_root=session_root,
                state=state,
                artifact=artifact,
                builder_mode=builder_mode,
                validation_report=validation_report,
            )

        page_handoff_report = _append_page_handoff(state)
        _append_artifact_lineage(state)
        state.progress_percent = max(state.progress_percent, 90)
        state.production_events.append(
            ProductionEvent(
                event_type="html_artifacts_built",
                stage=state.stage,
                message=f"Built, validated, previewed, and checked {len(built_artifacts)} HTML artifact(s).",
                metadata={
                    "artifact_ids": [artifact.artifact_id for artifact in built_artifacts],
                    "page_ids": [artifact.page_id for artifact in built_artifacts],
                    "build_mode": state.build_mode,
                    "page_handoff_status": page_handoff_report.status,
                    "last_qc_status": getattr(last_processing.get("qc_report"), "status", ""),
                },
            )
        )

    async def _build_html_artifact_for_page(
        self,
        *,
        session_root: Path,
        state: DesignProductionState,
        page: PageBlueprint,
        builder_mode: str,
        revision_request: dict[str, Any] | None = None,
        revision_impact: dict[str, Any] | None = None,
        validation_feedback: dict[str, Any] | None = None,
        previous_html_override: str | None = None,
        shared_html_context: str = "",
    ) -> HtmlArtifact:
        """Build one page-scoped HTML artifact with the selected builder."""
        if builder_mode in {"expert", "revision"}:
            return await self._build_expert_html_artifact(
                session_root=session_root,
                state=state,
                page=page,
                builder_mode=builder_mode,
                revision_request=revision_request,
                revision_impact=revision_impact,
                validation_feedback=validation_feedback,
                previous_html_override=previous_html_override,
                shared_html_context=shared_html_context,
            )
        return self.placeholder_builder.build(session_root=session_root, state=state, page=page)

    async def _validate_preview_and_qc_artifact(
        self,
        *,
        session_root: Path,
        state: DesignProductionState,
        artifact: HtmlArtifact,
        builder_mode: str,
        validation_report: HtmlValidationReport | None = None,
    ) -> dict[str, Any]:
        """Validate, preview, and quality-check one generated HTML artifact."""
        state.stage = "html_validation"
        validation_report = validation_report or self.html_validator.validate(
            artifact.path,
            session_root=session_root,
            artifact_id=artifact.artifact_id,
        )
        state.html_validation_reports.append(validation_report)
        if validation_report.status == "invalid":
            artifact.status = "failed"
            raise RuntimeError("; ".join(validation_report.issues) or "HTML validation failed")
        artifact.status = "valid"
        _append_component_inventory(state, artifact)
        state.stage = "design_system_extraction"
        extraction_report = _append_design_system_extraction(state, artifact)
        state.stage = "accessibility_check"
        accessibility_report = _append_accessibility_report(state, artifact)

        state.stage = "html_preview"
        preview_reports = await self.preview_renderer.render(
            artifact_id=artifact.artifact_id,
            html_path=artifact.path,
            output_dir=session_root / "previews",
            viewports=_preview_viewports_for_artifact(state, artifact),
        )
        state.preview_reports.extend(preview_reports)
        _append_browser_diagnostics(state, artifact)

        state.stage = "quality_check"
        expert_qc_report: DesignQcReport | None = None
        if builder_mode in {"expert", "revision"}:
            expert_qc_report = await self._assess_expert_quality(
                state=state,
                artifact=artifact,
                validation_report=validation_report,
                preview_reports=preview_reports,
            )
        qc_report = build_quality_report(
            artifact=artifact,
            validation_report=validation_report,
            preview_reports=preview_reports,
            brief=state.brief,
            layout_plan=state.layout_plan,
            accessibility_report=accessibility_report,
            expert_report=expert_qc_report,
        )
        state.qc_reports.append(qc_report)
        state.production_events.append(
            ProductionEvent(
                event_type="html_artifact_built",
                stage=state.stage,
                message="Built, validated, previewed, and checked one HTML artifact.",
                metadata={
                    "artifact_id": artifact.artifact_id,
                    "page_id": artifact.page_id,
                    "validation_status": validation_report.status,
                    "design_system_extraction_status": extraction_report.status,
                    "accessibility_status": accessibility_report.status,
                    "qc_status": qc_report.status,
                    "expert_qc_status": expert_qc_report.status if expert_qc_report is not None else "",
                },
            )
        )
        return {
            "validation_report": validation_report,
            "design_system_extraction_report": extraction_report,
            "accessibility_report": accessibility_report,
            "preview_reports": preview_reports,
            "qc_report": qc_report,
            "expert_qc_report": expert_qc_report,
        }

    async def _assess_expert_quality(
        self,
        *,
        state: DesignProductionState,
        artifact: HtmlArtifact,
        validation_report: HtmlValidationReport,
        preview_reports: list[PreviewReport],
    ) -> DesignQcReport:
        """Run supplemental expert QC without making the production flow fragile."""
        try:
            report = await self.expert_runtime.assess_quality(
                brief=state.brief,
                design_system=state.design_system,
                layout_plan=state.layout_plan,
                artifact=artifact,
                validation_report=validation_report,
                preview_reports=preview_reports,
                html=_read_artifact_html(artifact),
            )
            state.production_events.append(
                ProductionEvent(
                    event_type="expert_quality_assessed",
                    stage=state.stage,
                    message="DesignQCExpert completed supplemental quality assessment.",
                    metadata={"artifact_id": artifact.artifact_id, "expert_qc_status": report.status},
                )
            )
            return report
        except Exception as exc:
            message = f"DesignQCExpert failed: {type(exc).__name__}: {exc}"
            state.production_events.append(
                ProductionEvent(
                    event_type="expert_quality_failed",
                    stage=state.stage,
                    message=message,
                    metadata={"artifact_id": artifact.artifact_id},
                )
            )
            return DesignQcReport(
                artifact_ids=[artifact.artifact_id],
                status="warning",
                summary="DesignQCExpert was unavailable; deterministic QC completed.",
                findings=[
                    DesignQcFinding(
                        severity="warning",
                        category="technical",
                        target="DesignQCExpert",
                        summary=message,
                        recommendation="Review deterministic QC and rerun expert QC when the model runtime is available.",
                    )
                ],
            )

    async def _build_expert_html_artifact(
        self,
        *,
        session_root: Path,
        state: DesignProductionState,
        page: PageBlueprint | None = None,
        builder_mode: str,
        revision_request: dict[str, Any] | None = None,
        revision_impact: dict[str, Any] | None = None,
        validation_feedback: dict[str, Any] | None = None,
        previous_html_override: str | None = None,
        shared_html_context: str = "",
    ) -> HtmlArtifact:
        """Build and persist one HTML artifact with HtmlBuilderExpert."""
        if state.brief is None or state.design_system is None or state.layout_plan is None or not state.layout_plan.pages:
            raise ValueError("brief, design_system, and layout_plan are required before expert HTML generation")
        page = page or state.layout_plan.pages[0]
        layout_plan = _layout_plan_for_page(state.layout_plan, page)
        output_dir = session_root / "artifacts"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / _html_output_filename(
            page.path,
            revision=builder_mode == "revision",
            next_version=_next_html_version_for_page(state, page.page_id),
        )
        previous_html_context = previous_html_override if previous_html_override is not None else ""
        if previous_html_override is None and builder_mode == "revision":
            previous_html_context = _read_latest_html_for_page(state, page.page_id)
        html_output = await self.expert_runtime.build_html(
            brief=state.brief,
            design_system=state.design_system,
            layout_plan=layout_plan,
            reference_assets=state.reference_assets,
            build_mode="revision" if builder_mode == "revision" else "baseline",
            revision_request=revision_request,
            revision_impact=revision_impact,
            validation_feedback=validation_feedback,
            previous_html=previous_html_context,
            shared_html_context=shared_html_context,
        )
        output_path.write_text(html_output.html, encoding="utf-8")
        section_fragments = html_output.section_fragments or {
            section.section_id: section.title
            for section in page.sections
        }
        builder_name = "HtmlBuilderExpert.variant" if builder_mode == "revision" else "HtmlBuilderExpert.baseline"
        return HtmlArtifact(
            page_id=page.page_id,
            path=workspace_relative_path(output_path),
            builder=builder_name,
            section_fragments=section_fragments,
            depends_on=[asset.asset_id for asset in state.reference_assets if asset.status == "valid"],
            status="draft",
            metadata={
                "page_title": html_output.title or page.title,
                "expert_notes": html_output.notes,
                "model_name": getattr(self.expert_runtime, "model_name", ""),
                "build_mode": "revision" if builder_mode == "revision" else "baseline",
                "production_build_mode": state.build_mode,
                "revision_id": (revision_request or {}).get("revision_id", ""),
                "shared_html_context_used": bool(shared_html_context),
            },
        )

    def _finalize_artifacts(self, state: DesignProductionState) -> None:
        final_artifacts: list[WorkspaceFileRef] = []
        session_root = self.store.session_root(state.production_session)
        for html_artifact in _active_html_artifacts(state):
            final_artifacts.append(
                WorkspaceFileRef(
                    name=Path(html_artifact.path).name,
                    path=html_artifact.path,
                    description="Generated HTML design artifact.",
                    source=self.capability,
                )
            )
        for report in state.preview_reports:
            if report.screenshot_path:
                final_artifacts.append(
                    WorkspaceFileRef(
                        name=Path(report.screenshot_path).name,
                        path=report.screenshot_path,
                        description=f"{report.viewport.title()} preview screenshot.",
                        source=self.capability,
                    )
                )
        qc_path = f"{state.production_session.root_dir}/reports/qc_report.md"
        if state.qc_reports:
            state.qc_reports[-1].report_path = qc_path
            final_artifacts.append(
                WorkspaceFileRef(
                    name="qc_report.md",
                    path=qc_path,
                    description="Design quality report.",
                    source=self.capability,
                )
            )
        audit_path = f"{state.production_session.root_dir}/reports/design_system_audit.md"
        if state.design_system_audit_reports:
            state.design_system_audit_reports[-1].report_path = audit_path
            final_artifacts.append(
                WorkspaceFileRef(
                    name="design_system_audit.md",
                    path=audit_path,
                    description="Design system audit report.",
                    source=self.capability,
                )
            )
        inventory_md_path = f"{state.production_session.root_dir}/reports/component_inventory.md"
        inventory_json_path = f"{state.production_session.root_dir}/reports/component_inventory.json"
        if state.component_inventory_reports:
            state.component_inventory_reports[-1].report_path = inventory_md_path
            final_artifacts.extend(
                [
                    WorkspaceFileRef(
                        name="component_inventory.md",
                        path=inventory_md_path,
                        description="Implementation-facing component inventory report.",
                        source=self.capability,
                    ),
                    WorkspaceFileRef(
                        name="component_inventory.json",
                        path=inventory_json_path,
                        description="Machine-readable component inventory report.",
                        source=self.capability,
                    ),
                ]
            )
        extraction_md_path = f"{state.production_session.root_dir}/reports/design_system_extraction.md"
        extraction_json_path = f"{state.production_session.root_dir}/reports/design_system_extraction.json"
        if state.design_system_extraction_reports:
            state.design_system_extraction_reports[-1].report_path = extraction_md_path
            final_artifacts.extend(
                [
                    WorkspaceFileRef(
                        name="design_system_extraction.md",
                        path=extraction_md_path,
                        description="Extracted design-system usage report from generated HTML/CSS.",
                        source=self.capability,
                    ),
                    WorkspaceFileRef(
                        name="design_system_extraction.json",
                        path=extraction_json_path,
                        description="Machine-readable extracted design-system usage report.",
                        source=self.capability,
                    ),
                ]
            )
        accessibility_md_path = f"{state.production_session.root_dir}/reports/accessibility_report.md"
        accessibility_json_path = f"{state.production_session.root_dir}/reports/accessibility_report.json"
        if state.accessibility_reports:
            state.accessibility_reports[-1].report_path = accessibility_md_path
            final_artifacts.extend(
                [
                    WorkspaceFileRef(
                        name="accessibility_report.md",
                        path=accessibility_md_path,
                        description="Static HTML accessibility lint report.",
                        source=self.capability,
                    ),
                    WorkspaceFileRef(
                        name="accessibility_report.json",
                        path=accessibility_json_path,
                        description="Machine-readable HTML accessibility lint report.",
                        source=self.capability,
                    ),
                ]
            )
        diagnostics_md_path = f"{state.production_session.root_dir}/reports/browser_diagnostics.md"
        diagnostics_json_path = f"{state.production_session.root_dir}/reports/browser_diagnostics.json"
        if state.browser_diagnostics_reports:
            state.browser_diagnostics_reports[-1].report_path = diagnostics_md_path
            final_artifacts.extend(
                [
                    WorkspaceFileRef(
                        name="browser_diagnostics.md",
                        path=diagnostics_md_path,
                        description="Browser preview and export diagnostics report.",
                        source=self.capability,
                    ),
                    WorkspaceFileRef(
                        name="browser_diagnostics.json",
                        path=diagnostics_json_path,
                        description="Machine-readable browser preview and export diagnostics.",
                        source=self.capability,
                    ),
                ]
            )
        lineage_md_path = f"{state.production_session.root_dir}/reports/artifact_lineage.md"
        lineage_json_path = f"{state.production_session.root_dir}/reports/artifact_lineage.json"
        if state.artifact_lineage_reports:
            state.artifact_lineage_reports[-1].report_path = lineage_md_path
            final_artifacts.extend(
                [
                    WorkspaceFileRef(
                        name="artifact_lineage.md",
                        path=lineage_md_path,
                        description="Design HTML artifact lineage report.",
                        source=self.capability,
                    ),
                    WorkspaceFileRef(
                        name="artifact_lineage.json",
                        path=lineage_json_path,
                        description="Machine-readable Design HTML artifact lineage report.",
                        source=self.capability,
                    ),
                ]
            )
        page_handoff_md_path = f"{state.production_session.root_dir}/reports/page_handoff.md"
        page_handoff_json_path = f"{state.production_session.root_dir}/reports/page_handoff.json"
        if state.page_handoff_reports:
            state.page_handoff_reports[-1].report_path = page_handoff_md_path
            final_artifacts.extend(
                [
                    WorkspaceFileRef(
                        name="page_handoff.md",
                        path=page_handoff_md_path,
                        description="Design page and variant handoff readiness report.",
                        source=self.capability,
                    ),
                    WorkspaceFileRef(
                        name="page_handoff.json",
                        path=page_handoff_json_path,
                        description="Machine-readable Design page and variant handoff readiness report.",
                        source=self.capability,
                    ),
                ]
            )
        for report in state.pdf_export_reports:
            if report.status == "exported" and report.pdf_path:
                final_artifacts.append(
                    WorkspaceFileRef(
                        name=Path(report.pdf_path).name,
                        path=report.pdf_path,
                        description="PDF export generated from the approved HTML design.",
                        source=self.capability,
                    )
                )
        state.export_artifacts = write_handoff_exports(
            state=state,
            session_root=session_root,
            core_artifacts=final_artifacts,
        )
        final_artifacts.extend(state.export_artifacts)
        state.artifacts = final_artifacts

    async def _export_requested_pdf(self, state: DesignProductionState, *, response: dict[str, Any]) -> None:
        """Export the latest HTML to PDF when the session or approval requests it."""
        if not _pdf_export_requested(state, response):
            return
        latest_html = latest_html_artifact(state)
        if latest_html is None:
            state.pdf_export_reports.append(
                PdfExportReport(
                    artifact_id="",
                    source_html_path="",
                    status="failed",
                    issues=["PDF export requested but no HTML artifact exists."],
                )
            )
            return
        session_root = self.store.session_root(state.production_session)
        report = await self.pdf_exporter.export(
            artifact_id=latest_html.artifact_id,
            html_path=latest_html.path,
            output_path=session_root / "exports" / "design.pdf",
        )
        state.pdf_export_reports.append(report)
        state.production_events.append(
            ProductionEvent(
                event_type="pdf_export_completed" if report.status == "exported" else "pdf_export_unavailable",
                stage=state.stage,
                message=(
                    "Exported Design HTML to PDF."
                    if report.status == "exported"
                    else "Design PDF export was requested but could not be completed."
                ),
                metadata=report.model_dump(mode="json"),
            )
        )

    def _result_from_state(
        self,
        state: DesignProductionState,
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

    def _save_projection_files(self, state: DesignProductionState) -> None:
        """Write human-readable projection files derived from DesignProductionState."""
        root = self.store.session_root(state.production_session)
        _ensure_design_dirs(root)
        (root / "brief.md").write_text(_brief_markdown(state.brief), encoding="utf-8")
        (root / "design_system.json").write_text(
            json.dumps(
                state.design_system.model_dump(mode="json") if state.design_system is not None else None,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        latest_audit = state.design_system_audit_reports[-1] if state.design_system_audit_reports else None
        if latest_audit is not None:
            latest_audit.report_path = f"{state.production_session.root_dir}/reports/design_system_audit.md"
        (root / "reports" / "design_system_audit.json").write_text(
            json.dumps(
                [item.model_dump(mode="json") for item in state.design_system_audit_reports],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (root / "reports" / "design_system_audit.md").write_text(
            design_system_audit_markdown(latest_audit),
            encoding="utf-8",
        )
        latest_inventory = state.component_inventory_reports[-1] if state.component_inventory_reports else None
        if latest_inventory is not None:
            latest_inventory.report_path = f"{state.production_session.root_dir}/reports/component_inventory.md"
        (root / "reports" / "component_inventory.json").write_text(
            component_inventory_json(latest_inventory),
            encoding="utf-8",
        )
        (root / "reports" / "component_inventory.md").write_text(
            component_inventory_markdown(latest_inventory),
            encoding="utf-8",
        )
        latest_extraction = state.design_system_extraction_reports[-1] if state.design_system_extraction_reports else None
        if latest_extraction is not None:
            latest_extraction.report_path = f"{state.production_session.root_dir}/reports/design_system_extraction.md"
        (root / "reports" / "design_system_extraction.json").write_text(
            design_system_extraction_json(latest_extraction),
            encoding="utf-8",
        )
        (root / "reports" / "design_system_extraction.md").write_text(
            design_system_extraction_markdown(latest_extraction),
            encoding="utf-8",
        )
        latest_accessibility = state.accessibility_reports[-1] if state.accessibility_reports else None
        if latest_accessibility is not None:
            latest_accessibility.report_path = f"{state.production_session.root_dir}/reports/accessibility_report.md"
        (root / "reports" / "accessibility_report.json").write_text(
            accessibility_report_json(latest_accessibility),
            encoding="utf-8",
        )
        (root / "reports" / "accessibility_report.md").write_text(
            accessibility_report_markdown(latest_accessibility),
            encoding="utf-8",
        )
        (root / "layout_plan.json").write_text(
            json.dumps(
                state.layout_plan.model_dump(mode="json") if state.layout_plan is not None else None,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (root / "reports" / "html_validation.json").write_text(
            json.dumps([item.model_dump(mode="json") for item in state.html_validation_reports], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (root / "reports" / "preview_report.json").write_text(
            json.dumps([item.model_dump(mode="json") for item in state.preview_reports], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (root / "reports" / "pdf_export_report.json").write_text(
            json.dumps([item.model_dump(mode="json") for item in state.pdf_export_reports], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        latest_diagnostics = state.browser_diagnostics_reports[-1] if state.browser_diagnostics_reports else None
        if latest_diagnostics is not None:
            latest_diagnostics.report_path = f"{state.production_session.root_dir}/reports/browser_diagnostics.md"
        (root / "reports" / "browser_diagnostics.json").write_text(
            json.dumps(
                [item.model_dump(mode="json") for item in state.browser_diagnostics_reports],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (root / "reports" / "browser_diagnostics.md").write_text(
            browser_diagnostics_markdown(latest_diagnostics),
            encoding="utf-8",
        )
        latest_lineage = state.artifact_lineage_reports[-1] if state.artifact_lineage_reports else None
        if latest_lineage is not None:
            latest_lineage.report_path = f"{state.production_session.root_dir}/reports/artifact_lineage.md"
        (root / "reports" / "artifact_lineage.json").write_text(
            artifact_lineage_json(latest_lineage),
            encoding="utf-8",
        )
        (root / "reports" / "artifact_lineage.md").write_text(
            artifact_lineage_markdown(latest_lineage),
            encoding="utf-8",
        )
        latest_page_handoff = state.page_handoff_reports[-1] if state.page_handoff_reports else None
        if latest_page_handoff is not None:
            latest_page_handoff.report_path = f"{state.production_session.root_dir}/reports/page_handoff.md"
        (root / "reports" / "page_handoff.json").write_text(
            page_handoff_json(latest_page_handoff),
            encoding="utf-8",
        )
        (root / "reports" / "page_handoff.md").write_text(
            page_handoff_markdown(latest_page_handoff),
            encoding="utf-8",
        )
        latest_qc = state.qc_reports[-1] if state.qc_reports else None
        if latest_qc is not None:
            latest_qc.report_path = f"{state.production_session.root_dir}/reports/qc_report.md"
        (root / "reports" / "qc_report.json").write_text(
            json.dumps([item.model_dump(mode="json") for item in state.qc_reports], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (root / "reports" / "qc_report.md").write_text(
            quality_report_markdown(latest_qc),
            encoding="utf-8",
        )


def _ensure_design_dirs(session_root: Path) -> None:
    for child_name in ("artifacts", "previews", "reports", "exports"):
        (session_root / child_name).mkdir(parents=True, exist_ok=True)


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
    return get_active_production_session_id(adk_state, capability="design")


def _normalize_view_type(view_type: str | None) -> str | None:
    value = str(view_type or "overview").strip().lower() or "overview"
    return value if value in _VIEW_TYPES else None


def _build_mode_from_settings(design_settings: dict[str, Any]) -> DesignBuildMode:
    """Return the requested HTML build mode, defaulting to single-page output."""
    raw = str(
        design_settings.get("build_mode")
        or design_settings.get("html_build_mode")
        or design_settings.get("output_mode")
        or ""
    ).strip().lower().replace("-", "_")
    if raw in {"multi_html", "multi_page", "multipage", "multi"}:
        return "multi_html"
    if design_settings.get("multi_page") is True:
        return "multi_html"
    return "single_html"


def _target_pages_for_html_build(
    state: DesignProductionState,
    *,
    builder_mode: str,
    revision_impact: dict[str, Any] | None = None,
) -> list[PageBlueprint]:
    """Return the page targets for the current HTML build pass."""
    if state.layout_plan is None or not state.layout_plan.pages:
        raise ValueError("layout_plan with at least one page is required before HTML generation")
    if builder_mode == "revision" and state.build_mode == "multi_html":
        affected_page_ids = set(_revision_page_ids(state, revision_impact or {}))
        return [page for page in state.layout_plan.pages if page.page_id in affected_page_ids] or list(state.layout_plan.pages)
    if state.build_mode == "multi_html" and builder_mode != "revision":
        return list(state.layout_plan.pages)
    return [state.layout_plan.pages[0]]


def _layout_plan_for_page(layout_plan: LayoutPlan, page: PageBlueprint) -> LayoutPlan:
    """Return a one-page layout plan preserving the parent plan metadata."""
    return LayoutPlan(
        layout_plan_id=layout_plan.layout_plan_id,
        version=layout_plan.version,
        pages=[page],
        global_notes=layout_plan.global_notes,
    )


def _preview_viewports_for_artifact(state: DesignProductionState, artifact: HtmlArtifact) -> list[ViewportSpec]:
    """Return preview viewports requested by the artifact's page targets."""
    page = _page_for_artifact(state, artifact)
    raw_targets = page.device_targets if page is not None else []
    targets = [str(target or "").strip().lower() for target in raw_targets]
    if not targets:
        targets = list(_DEFAULT_PREVIEW_DEVICE_TARGETS)
    viewports: list[ViewportSpec] = []
    seen: set[str] = set()
    for target in targets:
        viewport = _PREVIEW_VIEWPORT_BY_DEVICE.get(target)
        if viewport is None or viewport.name in seen:
            continue
        viewports.append(viewport)
        seen.add(viewport.name)
    if viewports:
        return viewports
    return [_PREVIEW_VIEWPORT_BY_DEVICE[name] for name in _DEFAULT_PREVIEW_DEVICE_TARGETS]


def _page_for_artifact(state: DesignProductionState, artifact: HtmlArtifact) -> PageBlueprint | None:
    """Return the planned page associated with one HTML artifact."""
    pages = state.layout_plan.pages if state.layout_plan is not None else []
    for page in pages:
        if page.page_id == artifact.page_id:
            return page
    return None


def _active_html_artifacts(state: DesignProductionState) -> list[HtmlArtifact]:
    """Return active HTML artifacts, falling back to the latest draft artifact."""
    active = [artifact for artifact in state.html_artifacts if artifact.status in {"valid", "approved"}]
    if active:
        return active
    return state.html_artifacts[-1:] if state.html_artifacts else []


def _revision_page_ids(state: DesignProductionState, revision_impact: dict[str, Any]) -> list[str]:
    """Return page ids that should be rebuilt for a revision."""
    pages = state.layout_plan.pages if state.layout_plan is not None else []
    if not pages:
        return []
    if state.build_mode != "multi_html":
        return [pages[0].page_id]
    requested_ids = {
        str(page_id).strip()
        for page_id in revision_impact.get("affected_page_ids", [])
        if str(page_id).strip()
    }
    if requested_ids:
        return [page.page_id for page in pages if page.page_id in requested_ids]
    return [page.page_id for page in pages]


def _normalize_page_path(path: str | None) -> str:
    """Return a safe single-file HTML artifact path."""
    value = str(path or "").strip() or "index.html"
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        return "index.html"
    if candidate.suffix.lower() != ".html":
        return "index.html"
    return candidate.name


def _html_output_filename(path: str | None, *, revision: bool, next_version: int) -> str:
    """Return the artifact filename for a baseline or revision HTML build."""
    safe_name = _normalize_page_path(path)
    if not revision:
        return safe_name
    candidate = Path(safe_name)
    version = max(int(next_version or 2), 2)
    return f"{candidate.stem}_v{version}{candidate.suffix}"


def _next_html_version_for_page(state: DesignProductionState, page_id: str) -> int:
    """Return the next artifact version number for one page."""
    page_artifact_count = sum(1 for artifact in state.html_artifacts if artifact.page_id == page_id)
    return page_artifact_count + 1


def _revision_history_entry(
    revision_request: dict[str, Any],
    *,
    impact_view: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a compact persisted revision history entry."""
    impact = impact_view or {}
    entry: dict[str, Any] = {
        "revision_id": str(impact.get("revision_id") or revision_request.get("revision_id") or ""),
        "notes": str(revision_request.get("notes") or ""),
        "targets": _compact_revision_targets(revision_request.get("targets") or []),
    }
    decision = str(revision_request.get("decision") or "").strip()
    if decision:
        entry["decision"] = decision
    if impact:
        entry["impact_summary"] = {
            "affected_brief": bool(impact.get("affected_brief")),
            "affected_design_system": bool(impact.get("affected_design_system")),
            "affected_page_ids": list(impact.get("affected_page_ids") or []),
            "affected_section_ids": list(impact.get("affected_section_ids") or []),
            "affected_asset_ids": list(impact.get("affected_asset_ids") or []),
            "affected_artifact_ids": list(impact.get("affected_artifact_ids") or []),
            "recommended_action": str(impact.get("recommended_action") or ""),
        }
    return entry


def _compact_revision_targets(targets: Any) -> list[Any]:
    """Return revision targets without carrying arbitrary request payloads."""
    if not isinstance(targets, list):
        return []
    compacted: list[Any] = []
    allowed_keys = ("kind", "type", "id", "label", "path")
    for target in targets:
        if isinstance(target, dict):
            item = {
                key: str(target.get(key) or "").strip()
                for key in allowed_keys
                if str(target.get(key) or "").strip()
            }
            if item:
                compacted.append(item)
            continue
        text = str(target or "").strip()
        if text:
            compacted.append(text)
    return compacted


def _read_latest_html(state: DesignProductionState) -> str:
    """Read the latest generated HTML artifact for revision context."""
    if not state.html_artifacts:
        return ""
    latest_path = state.html_artifacts[-1].path
    if not latest_path:
        return ""
    try:
        return resolve_workspace_path(latest_path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_latest_html_for_page(state: DesignProductionState, page_id: str) -> str:
    """Read the latest generated HTML artifact for one page."""
    for artifact in reversed(state.html_artifacts):
        if artifact.page_id == page_id and artifact.path:
            try:
                return resolve_workspace_path(artifact.path).read_text(encoding="utf-8")
            except OSError:
                return ""
    return _read_latest_html(state)


def _read_artifact_html(artifact: HtmlArtifact) -> str:
    """Read one HTML artifact for quality assessment context."""
    try:
        return resolve_workspace_path(artifact.path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _shared_html_context_for_artifacts(artifacts: list[HtmlArtifact]) -> str:
    """Return compact shared markup context from already-built pages."""
    entries: list[str] = []
    for artifact in artifacts:
        fragments = _shared_html_fragments(_read_artifact_html(artifact))
        if not fragments:
            continue
        artifact_name = Path(artifact.path).name
        entries.append(f"{artifact_name} ({artifact.artifact_id}):\n{fragments}")
    return "\n\n".join(entries)


def _shared_html_fragments(html: str, *, max_chars: int = 5000) -> str:
    """Extract repeated page chrome fragments for multi-page consistency prompts."""
    fragments: list[str] = []
    for tag in ("header", "nav", "footer"):
        match = re.search(rf"<{tag}\b.*?</{tag}>", html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            fragments.append(_compact_html_fragment(match.group(0), max_chars=1800))
    text = "\n".join(fragments)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()} ... [truncated]"


def _compact_html_fragment(html: str, *, max_chars: int) -> str:
    """Return one HTML fragment as compact prompt text."""
    text = " ".join(str(html or "").split())
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()} ... [truncated]"


def _html_validation_repair_feedback(report: HtmlValidationReport) -> dict[str, Any]:
    """Return compact validation feedback for one HtmlBuilderExpert repair pass."""
    return {
        "status": report.status,
        "path": report.path,
        "issues": report.issues,
        "warnings": report.warnings,
        "repair_instruction": "Return a complete corrected HTML document that resolves every validation issue.",
    }


def _append_design_system_audit(state: DesignProductionState) -> DesignSystemAuditReport:
    """Audit the current design system and store the report on production state."""
    report = audit_design_system(state.design_system)
    state.design_system_audit_reports.append(report)
    state.production_events.append(
        ProductionEvent(
            event_type="design_system_audited",
            stage=state.stage,
            message="Audited Design system tokens for handoff readiness.",
            metadata={
                "report_id": report.report_id,
                "status": report.status,
                "finding_counts": report.metrics.get("finding_counts", {}),
            },
        )
    )
    return report


def _append_component_inventory(state: DesignProductionState, artifact: HtmlArtifact) -> ComponentInventoryReport:
    """Build and store a component inventory report for one HTML artifact."""
    report = build_component_inventory(state, artifact=artifact)
    state.component_inventory_reports.append(report)
    state.production_events.append(
        ProductionEvent(
            event_type="component_inventory_built",
            stage=state.stage,
            message="Built Design component inventory for handoff.",
            metadata={
                "report_id": report.report_id,
                "artifact_id": report.artifact_id,
                "status": report.status,
                "item_count": report.metrics.get("item_count", 0),
            },
        )
    )
    return report


def _append_design_system_extraction(state: DesignProductionState, artifact: HtmlArtifact) -> DesignSystemExtractionReport:
    """Build and store a design-system extraction report for one HTML artifact."""
    report = build_design_system_extraction(state, artifact=artifact)
    state.design_system_extraction_reports.append(report)
    state.production_events.append(
        ProductionEvent(
            event_type="design_system_extraction_built",
            stage=state.stage,
            message="Extracted Design system usage from generated HTML/CSS.",
            metadata={
                "report_id": report.report_id,
                "artifact_id": report.artifact_id,
                "status": report.status,
                "token_count": report.metrics.get("token_count", 0),
                "selector_count": report.metrics.get("selector_count", 0),
            },
        )
    )
    return report


def _append_accessibility_report(state: DesignProductionState, artifact: HtmlArtifact) -> AccessibilityReport:
    """Build and store an accessibility report for one HTML artifact."""
    report = build_accessibility_report(state, artifact=artifact)
    state.accessibility_reports.append(report)
    state.production_events.append(
        ProductionEvent(
            event_type="accessibility_report_built",
            stage=state.stage,
            message="Built static HTML accessibility report.",
            metadata={
                "report_id": report.report_id,
                "artifact_id": report.artifact_id,
                "status": report.status,
                "finding_counts": report.metrics.get("finding_counts", {}),
            },
        )
    )
    return report


def _append_browser_diagnostics(
    state: DesignProductionState,
    artifact: HtmlArtifact | None,
) -> BrowserDiagnosticsReport:
    """Build and store the latest browser diagnostics for one HTML artifact."""
    report = build_browser_diagnostics(state, artifact=artifact)
    if report.artifact_id:
        state.browser_diagnostics_reports = [
            item
            for item in state.browser_diagnostics_reports
            if item.artifact_id != report.artifact_id
        ]
    state.browser_diagnostics_reports.append(report)
    state.production_events.append(
        ProductionEvent(
            event_type="browser_diagnostics_built",
            stage=state.stage,
            message="Built browser preview and export diagnostics.",
            metadata={
                "report_id": report.report_id,
                "artifact_id": report.artifact_id,
                "status": report.status,
                "finding_counts": report.metrics.get("finding_counts", {}),
            },
        )
    )
    return report


def _append_artifact_lineage(state: DesignProductionState) -> ArtifactLineageReport:
    """Build and store the latest artifact lineage report."""
    report = build_artifact_lineage(state)
    report.report_path = f"{state.production_session.root_dir}/reports/artifact_lineage.md"
    state.artifact_lineage_reports = [report]
    state.production_events.append(
        ProductionEvent(
            event_type="artifact_lineage_built",
            stage=state.stage,
            message="Built Design HTML artifact lineage report.",
            metadata={
                "report_id": report.report_id,
                "status": report.status,
                "latest_artifact_id": report.latest_artifact_id,
                "artifact_count": report.metrics.get("artifact_count", 0),
            },
        )
    )
    return report


def _append_page_handoff(state: DesignProductionState) -> PageHandoffReport:
    """Build and store the latest page handoff readiness report."""
    report = build_page_handoff(state)
    report.report_path = f"{state.production_session.root_dir}/reports/page_handoff.md"
    state.page_handoff_reports = [report]
    state.production_events.append(
        ProductionEvent(
            event_type="page_handoff_built",
            stage=state.stage,
            message="Built page and variant handoff readiness report.",
            metadata={
                "report_id": report.report_id,
                "status": report.status,
                "ready_item_count": report.metrics.get("ready_item_count", 0),
                "handoff_item_count": report.metrics.get("handoff_item_count", 0),
            },
        )
    )
    return report


def _revision_stale_reason(revision_request: dict[str, Any]) -> str:
    """Return a concise stale reason for artifacts replaced by a revision."""
    notes = str(revision_request.get("notes") or "").strip()
    if not notes:
        return "Revision applied; affected page artifact rebuilt."
    return f"Revision applied; affected page artifact rebuilt. Notes: {notes[:160]}"


def _status_message(state: DesignProductionState) -> str:
    if state.status == "completed":
        return "Design production is completed."
    if state.status == "needs_user_review":
        return f"Design production is waiting for review at {state.stage}."
    if state.status == "failed":
        return "Design production failed."
    return f"Design production is {state.status} at {state.stage}."


def _normalize_user_response(value: Any | None) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = str(value or "").strip()
    if text.lower() in {"approve", "approved", "yes", "confirm", "确认", "同意"}:
        return {"decision": "approve"}
    if text.lower() in {"cancel", "取消"}:
        return {"decision": "cancel"}
    if text:
        return {"decision": "revise", "notes": text}
    return {}


def _normalize_resume_decision(response: dict[str, Any]) -> str:
    decision = str(response.get("decision") or response.get("action") or "").strip().lower()
    if decision in {"approved", "confirm", "yes", "complete", "completed"}:
        return "approve"
    if decision in {"revise", "change", "edit", "修改"}:
        return "revise"
    if decision in {"cancel", "stop", "取消"}:
        return "cancel"
    if decision == "approve":
        return "approve"
    return ""


def _requested_exports_from_settings(design_settings: dict[str, Any]) -> list[str]:
    return _normalized_export_names(design_settings)


def _pdf_export_requested(state: DesignProductionState, response: dict[str, Any]) -> bool:
    for export_name in _normalized_export_names(response):
        if export_name not in state.requested_exports:
            state.requested_exports.append(export_name)
    return "pdf" in set(state.requested_exports)


def _normalized_export_names(payload: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("exports", "outputs", "export_formats", "requested_exports"):
        raw = payload.get(key)
        if isinstance(raw, (list, tuple, set)):
            values.extend(raw)
        elif raw:
            values.append(raw)
    if payload.get("export_pdf") or payload.get("pdf_export"):
        values.append("pdf")

    names: list[str] = []
    for value in values:
        normalized = str(value).strip().lower().replace("-", "_")
        if normalized in {"pdf", "html_pdf", "print_pdf"} and "pdf" not in names:
            names.append("pdf")
    return names


def _infer_design_genre(user_prompt: str, design_settings: dict[str, Any]) -> str:
    explicit = str(design_settings.get("design_genre") or design_settings.get("genre") or "").strip()
    if explicit in {"landing_page", "ui_design", "product_detail_page", "micro_site", "one_pager", "prototype", "wireframe"}:
        return explicit
    lowered = user_prompt.lower()
    if any(token in lowered for token in ("dashboard", "admin", "ui", "app screen", "看板", "后台", "界面")):
        return "ui_design"
    if any(token in lowered for token in ("pdp", "product detail", "详情页", "商品页", "产品页")):
        return "product_detail_page"
    return "landing_page"


def _prepare_placeholder_planning_state(
    state: DesignProductionState,
    *,
    user_prompt: str,
    design_settings: dict[str, Any],
) -> None:
    genre = state.design_genre or _infer_design_genre(user_prompt, design_settings)
    state.stage = "layout_prepared"
    state.progress_percent = 35
    state.brief = DesignBrief(
        design_genre=genre,
        goal=_goal_from_prompt(user_prompt, genre=genre),
        audience=str(design_settings.get("audience") or _default_audience(genre)),
        primary_action=str(design_settings.get("primary_action") or _default_primary_action(genre)),
        selling_points=_selling_points_from_prompt(user_prompt),
        content_requirements=[
            "One HTML file per planned page" if state.build_mode == "multi_html" else "Single-file HTML",
            "Responsive desktop and mobile layout",
            "Stable section ids",
        ],
        constraints=["No local absolute paths", "No required external runtime dependency"],
        notes="P0a placeholder brief generated deterministically for production pipeline validation.",
    )
    state.design_system = DesignSystemSpec(
        source="placeholder",
        colors=[
            DesignTokenColor(name="primary", value="#165DFF", usage="Primary actions and emphasis"),
            DesignTokenColor(name="accent", value="#00A878", usage="Positive highlights"),
            DesignTokenColor(name="ink", value="#18202F", usage="Body text"),
            DesignTokenColor(name="surface", value="#F6F8FB", usage="Page background"),
        ],
        typography=[
            DesignTokenTypography(role="display", font_family="Inter, system-ui, sans-serif", font_size_px=56, font_weight="820", line_height="1.02"),
            DesignTokenTypography(role="body", font_family="Inter, system-ui, sans-serif", font_size_px=17, font_weight="400", line_height="1.55"),
        ],
        spacing={"section_y": "32px", "content_gap": "18px"},
        radii={"default": "8px"},
        shadows={},
        component_tokens={"button": {"height": "40px", "radius": "8px"}},
        notes="P0a placeholder design system.",
    )
    state.layout_plan = _placeholder_layout_plan(
        genre=genre,
        brief=state.brief,
        build_mode=state.build_mode,
        design_settings=design_settings,
    )
    _append_design_system_audit(state)
    state.production_events.append(
        ProductionEvent(
            event_type="planning_state_prepared",
            stage=state.stage,
            message="Prepared deterministic Design brief, design system, and layout plan.",
        )
    )


def _goal_from_prompt(user_prompt: str, *, genre: str) -> str:
    prompt = user_prompt.strip()
    if prompt:
        return prompt[:180]
    defaults = {
        "ui_design": "Operational interface design with clear hierarchy and efficient scanning",
        "product_detail_page": "Product detail page that explains value and supports purchase intent",
        "landing_page": "Landing page design that communicates the offer and drives conversion",
    }
    return defaults.get(genre, defaults["landing_page"])


def _default_audience(genre: str) -> str:
    if genre == "ui_design":
        return "daily product users and operations teams"
    if genre == "product_detail_page":
        return "prospective buyers comparing product value"
    return "prospective customers evaluating the offer"


def _default_primary_action(genre: str) -> str:
    if genre == "ui_design":
        return "Review dashboard"
    if genre == "product_detail_page":
        return "Contact sales"
    return "Request demo"


def _selling_points_from_prompt(user_prompt: str) -> list[str]:
    cleaned = user_prompt.strip()
    if not cleaned:
        return ["Clear value proposition", "Responsive structure", "Reviewable HTML artifact"]
    return [cleaned[:80], "Reviewable production state", "Portable HTML artifacts"]


def _placeholder_layout_plan(
    *,
    genre: str,
    brief: DesignBrief,
    build_mode: DesignBuildMode,
    design_settings: dict[str, Any],
) -> LayoutPlan:
    page_specs = _placeholder_page_specs(genre=genre, build_mode=build_mode, design_settings=design_settings)
    pages = [
        PageBlueprint(
            title=spec["title"],
            path=spec["path"],
            sections=_placeholder_sections(
                genre=genre,
                brief=brief,
                section_titles=spec["sections"],
                page_slug=Path(spec["path"]).stem,
            ),
            device_targets=brief.device_targets,
        )
        for spec in page_specs
    ]
    notes = "P0a multi-page placeholder layout." if build_mode == "multi_html" else "P0a single-page placeholder layout."
    return LayoutPlan(pages=pages, global_notes=notes)


def _placeholder_page_specs(
    *,
    genre: str,
    build_mode: DesignBuildMode,
    design_settings: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_pages = design_settings.get("pages")
    specs: list[dict[str, Any]] = []
    if isinstance(raw_pages, list):
        for index, raw_page in enumerate(raw_pages, start=1):
            if not isinstance(raw_page, dict):
                continue
            title = str(raw_page.get("title") or raw_page.get("name") or f"Page {index}").strip() or f"Page {index}"
            path = _normalize_page_path(str(raw_page.get("path") or _path_from_title(title, index=index)))
            sections = _section_titles_from_page_settings(raw_page.get("sections"))
            specs.append({"title": title, "path": path, "sections": sections or _default_section_titles(genre)})
    if specs:
        return specs if build_mode == "multi_html" else [specs[0]]
    if build_mode == "multi_html":
        return [
            {"title": _page_title(genre), "path": "index.html", "sections": _default_section_titles(genre)},
            {"title": "Design Details", "path": "details.html", "sections": ["Overview", "Interaction Model", "Responsive States"]},
            {"title": "Conversion Path", "path": "conversion.html", "sections": ["Offer", "Proof", "Final Action"]},
        ]
    return [{"title": _page_title(genre), "path": "index.html", "sections": _default_section_titles(genre)}]


def _placeholder_sections(
    *,
    genre: str,
    brief: DesignBrief,
    section_titles: list[str],
    page_slug: str,
) -> list[LayoutSection]:
    return [
        LayoutSection(
            section_id=_stable_section_id(f"{page_slug}-{title}" if page_slug != "index" else title),
            title=title,
            purpose=_section_purpose(title, genre=genre),
            content=[
                f"Supports goal: {brief.goal[:80]}",
                "Uses deterministic P0a content for pipeline validation.",
                "Can be replaced by real Design experts in P0b.",
            ],
            responsive_notes="Stack content vertically on mobile.",
        )
        for title in section_titles
    ]


def _default_section_titles(genre: str) -> list[str]:
    section_titles = {
        "ui_design": ["Top Filters", "KPI Overview", "Operational Detail", "Alerts and Next Actions"],
        "product_detail_page": ["Product Hero", "Core Benefits", "Scenario Details", "Final Contact CTA"],
        "landing_page": ["Hero", "Value Proposition", "Feature System", "Final CTA"],
    }.get(genre, ["Hero", "Content", "Proof", "CTA"])
    return section_titles


def _section_titles_from_page_settings(raw_sections: Any) -> list[str]:
    if not isinstance(raw_sections, list):
        return []
    titles: list[str] = []
    for index, raw_section in enumerate(raw_sections, start=1):
        if isinstance(raw_section, dict):
            title = str(raw_section.get("title") or raw_section.get("name") or f"Section {index}").strip()
        else:
            title = str(raw_section).strip()
        if title:
            titles.append(title)
    return titles


def _path_from_title(title: str, *, index: int) -> str:
    slug = _stable_section_id(title).strip("-") or f"page-{index}"
    if index == 1 and slug in {"home", "index", "landing-page", "design-landing-page"}:
        return "index.html"
    return f"{slug}.html"


def _stable_section_id(title: str) -> str:
    return title.lower().replace(" and ", "-").replace(" ", "-").replace("/", "-")


def _section_purpose(title: str, *, genre: str) -> str:
    if genre == "ui_design":
        return f"Provide a scannable {title.lower()} area for repeated operational use."
    if genre == "product_detail_page":
        return f"Explain the {title.lower()} part of the product decision journey."
    return f"Support the landing page through the {title.lower()} section."


def _page_title(genre: str) -> str:
    if genre == "ui_design":
        return "Design UI Mockup"
    if genre == "product_detail_page":
        return "Design Product Detail Page"
    return "Design Landing Page"


def _design_direction_review_payload(state: DesignProductionState) -> ReviewPayload:
    return ReviewPayload(
        review_type="design_direction_review",
        title="Confirm design direction",
        summary="Review the brief, visual system, and page layout before HTML generation.",
        items=[
            {"kind": "brief", "brief": state.brief.model_dump(mode="json") if state.brief is not None else None},
            {
                "kind": "design_system",
                "design_system": state.design_system.model_dump(mode="json") if state.design_system is not None else None,
            },
            {"kind": "layout_plan", "layout_plan": state.layout_plan.model_dump(mode="json") if state.layout_plan is not None else None},
            {"kind": "reference_assets", "assets": [item.model_dump(mode="json") for item in state.reference_assets]},
        ],
        options=[
            {"id": "approve", "label": "Approve and build HTML"},
            {"id": "revise", "label": "Revise direction"},
            {"id": "cancel", "label": "Cancel"},
        ],
    )


def _preview_review_payload(state: DesignProductionState) -> ReviewPayload:
    return ReviewPayload(
        review_type="preview_review",
        title="Review generated HTML design",
        summary="Open the HTML artifact and preview reports, then approve or request changes.",
        items=[
            {"kind": "html_artifacts", "artifacts": [_html_artifact_payload(state, item) for item in state.html_artifacts]},
            {"kind": "preview_reports", "reports": [_preview_report_payload(state, item) for item in state.preview_reports]},
            {"kind": "qc_reports", "reports": [item.model_dump(mode="json") for item in state.qc_reports]},
            {"kind": "page_handoff_reports", "reports": [item.model_dump(mode="json") for item in state.page_handoff_reports]},
        ],
        options=[
            {"id": "approve", "label": "Approve final design"},
            {"id": "revise", "label": "Request changes"},
            {"id": "cancel", "label": "Cancel"},
        ],
        metadata=_preview_review_metadata(state),
    )


def _preview_review_metadata(state: DesignProductionState) -> dict[str, Any]:
    """Return compact delivery, preview, quality, and source context for review."""
    latest_html = latest_html_artifact(state)
    artifact_id = latest_html.artifact_id if latest_html is not None else ""
    preview_reports = _preview_reports_for_artifact(state, artifact_id)
    validation_report = _latest_validation_report_for_artifact(state, artifact_id)
    qc_report = _latest_qc_report_for_artifact(state, artifact_id)
    extraction_report = _latest_design_system_extraction_report_for_artifact(state, artifact_id)
    accessibility_report = _latest_accessibility_report_for_artifact(state, artifact_id)
    diagnostics_report = _latest_browser_diagnostics_report_for_artifact(state, artifact_id)
    lineage_report = state.artifact_lineage_reports[-1] if state.artifact_lineage_reports else None
    page_handoff_report = state.page_handoff_reports[-1] if state.page_handoff_reports else None
    source_refs = list(latest_html.depends_on) if latest_html is not None else []
    return {
        "delivery": {
            "latest_html_artifact_id": artifact_id,
            "latest_html_path": latest_html.path if latest_html is not None else "",
            "html_status": latest_html.status if latest_html is not None else "",
            "preview_count": len(preview_reports),
            "screenshot_count": len([report for report in preview_reports if report.screenshot_path]),
            "html_validation_status": validation_report.status if validation_report is not None else "",
            "html_validation_report_path": (
                f"{state.production_session.root_dir}/reports/html_validation.json"
                if validation_report is not None
                else ""
            ),
            "qc_status": qc_report.status if qc_report is not None else "",
            "qc_report_path": (
                qc_report.report_path or f"{state.production_session.root_dir}/reports/qc_report.md"
                if qc_report is not None
                else ""
            ),
            "accessibility_status": accessibility_report.status if accessibility_report is not None else "",
            "accessibility_report_path": (
                accessibility_report.report_path or f"{state.production_session.root_dir}/reports/accessibility_report.md"
                if accessibility_report is not None
                else ""
            ),
            "design_system_extraction_status": extraction_report.status if extraction_report is not None else "",
            "design_system_extraction_report_path": (
                extraction_report.report_path or f"{state.production_session.root_dir}/reports/design_system_extraction.md"
                if extraction_report is not None
                else ""
            ),
            "page_handoff_status": page_handoff_report.status if page_handoff_report is not None else "",
            "page_handoff_report_path": (
                page_handoff_report.report_path or f"{state.production_session.root_dir}/reports/page_handoff.md"
                if page_handoff_report is not None
                else ""
            ),
        },
        "preview": _preview_review_summary(preview_reports),
        "design_system_extraction": _design_system_extraction_summary(extraction_report),
        "accessibility": _accessibility_summary(accessibility_report),
        "diagnostics": _browser_diagnostics_summary(diagnostics_report),
        "lineage": _artifact_lineage_summary(lineage_report),
        "pages": _page_handoff_summary(page_handoff_report),
        "quality": _quality_review_summary(qc_report),
        "source_refs": source_refs,
        "source_ref_details": source_ref_details(state, source_refs),
    }


def _preview_reports_for_artifact(state: DesignProductionState, artifact_id: str) -> list[PreviewReport]:
    if not artifact_id:
        return list(state.preview_reports)
    return [report for report in state.preview_reports if report.artifact_id == artifact_id]


def _latest_validation_report_for_artifact(
    state: DesignProductionState,
    artifact_id: str,
) -> HtmlValidationReport | None:
    for report in reversed(state.html_validation_reports):
        if not artifact_id or report.artifact_id == artifact_id:
            return report
    return None


def _latest_qc_report_for_artifact(
    state: DesignProductionState,
    artifact_id: str,
) -> DesignQcReport | None:
    for report in reversed(state.qc_reports):
        if not artifact_id or artifact_id in report.artifact_ids:
            return report
    return None


def _latest_design_system_extraction_report_for_artifact(
    state: DesignProductionState,
    artifact_id: str,
) -> DesignSystemExtractionReport | None:
    for report in reversed(state.design_system_extraction_reports):
        if not artifact_id or report.artifact_id == artifact_id:
            return report
    return None


def _latest_accessibility_report_for_artifact(
    state: DesignProductionState,
    artifact_id: str,
) -> AccessibilityReport | None:
    for report in reversed(state.accessibility_reports):
        if not artifact_id or report.artifact_id == artifact_id:
            return report
    return None


def _latest_browser_diagnostics_report_for_artifact(
    state: DesignProductionState,
    artifact_id: str,
) -> BrowserDiagnosticsReport | None:
    for report in reversed(state.browser_diagnostics_reports):
        if not artifact_id or report.artifact_id == artifact_id:
            return report
    return None


def _preview_review_summary(preview_reports: list[PreviewReport]) -> dict[str, Any]:
    """Build a concise browser-preview summary for review metadata."""
    attention_reports = []
    reports = []
    for report in preview_reports:
        item = {
            "report_id": report.report_id,
            "viewport": report.viewport,
            "valid": report.valid,
            "screenshot_path": report.screenshot_path,
            "issue_count": len(report.issues),
            "console_error_count": len(report.console_errors),
            "network_failure_count": len(report.network_failures),
            "layout": _layout_metric_summary(report.layout_metrics),
        }
        reports.append(item)
        if (
            not report.valid
            or report.issues
            or report.console_errors
            or report.network_failures
        ):
            attention_reports.append(item)
    return {
        "report_count": len(preview_reports),
        "valid_count": len([report for report in preview_reports if report.valid]),
        "screenshot_count": len([report for report in preview_reports if report.screenshot_path]),
        "reports": reports,
        "attention_reports": attention_reports,
    }


def _layout_metric_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    """Return stable preview layout metrics without leaking browser internals."""
    viewport_width = metrics.get("viewportWidth")
    body_scroll_width = metrics.get("bodyScrollWidth")
    horizontal_overflow_px = 0
    if isinstance(viewport_width, int) and isinstance(body_scroll_width, int):
        horizontal_overflow_px = max(0, body_scroll_width - viewport_width)
    return {
        "viewport_width": viewport_width,
        "body_scroll_width": body_scroll_width,
        "body_scroll_height": metrics.get("bodyScrollHeight"),
        "horizontal_overflow_px": horizontal_overflow_px,
    }


def _design_system_extraction_summary(report: DesignSystemExtractionReport | None) -> dict[str, Any]:
    """Build a compact design-system extraction summary for review metadata."""
    if report is None:
        return {
            "status": "",
            "summary": "",
            "report_path": "",
            "token_count": 0,
            "selector_count": 0,
            "token_source_counts": {},
        }
    return {
        "status": report.status,
        "summary": report.summary,
        "report_path": report.report_path or "",
        "token_count": report.metrics.get("token_count", 0),
        "selector_count": report.metrics.get("selector_count", 0),
        "token_source_counts": report.metrics.get("token_source_counts", {}),
    }


def _accessibility_summary(report: AccessibilityReport | None) -> dict[str, Any]:
    """Build a compact accessibility summary for review metadata."""
    finding_counts = {"info": 0, "warning": 0, "error": 0}
    if report is None:
        return {
            "status": "",
            "summary": "",
            "report_path": "",
            "finding_counts": finding_counts,
            "attention_findings": [],
        }
    finding_counts.update(report.metrics.get("finding_counts", {}))
    return {
        "status": report.status,
        "summary": report.summary,
        "report_path": report.report_path or "",
        "finding_counts": finding_counts,
        "attention_findings": [
            {
                "finding_id": finding.finding_id,
                "severity": finding.severity,
                "category": finding.category,
                "target": finding.target,
                "summary": finding.summary,
            }
            for finding in report.findings
            if finding.severity in {"warning", "error"}
        ],
    }


def _browser_diagnostics_summary(report: BrowserDiagnosticsReport | None) -> dict[str, Any]:
    """Build a compact browser diagnostics summary for review metadata."""
    finding_counts = {"info": 0, "warning": 0, "error": 0}
    if report is None:
        return {
            "status": "",
            "summary": "",
            "report_path": "",
            "finding_counts": finding_counts,
            "attention_findings": [],
        }
    finding_counts.update(report.metrics.get("finding_counts", {}))
    return {
        "status": report.status,
        "summary": report.summary,
        "report_path": report.report_path or "",
        "finding_counts": finding_counts,
        "attention_findings": [
            {
                "finding_id": finding.finding_id,
                "severity": finding.severity,
                "category": finding.category,
                "target": finding.target,
                "summary": finding.summary,
                "recommendation": finding.recommendation,
            }
            for finding in report.findings
            if finding.severity in {"warning", "error"}
        ],
    }


def _artifact_lineage_summary(report: ArtifactLineageReport | None) -> dict[str, Any]:
    """Build a compact artifact lineage summary for review metadata."""
    if report is None:
        return {
            "status": "",
            "summary": "",
            "latest_artifact_id": "",
            "report_path": "",
            "artifact_count": 0,
            "revision_count": 0,
        }
    return {
        "status": report.status,
        "summary": report.summary,
        "latest_artifact_id": report.latest_artifact_id,
        "report_path": report.report_path or "",
        "artifact_count": report.metrics.get("artifact_count", 0),
        "revision_count": report.metrics.get("revision_count", 0),
    }


def _page_handoff_summary(report: PageHandoffReport | None) -> dict[str, Any]:
    """Build a compact page handoff summary for review metadata."""
    if report is None:
        return {
            "status": "",
            "summary": "",
            "report_path": "",
            "planned_page_count": 0,
            "handoff_item_count": 0,
            "ready_item_count": 0,
            "missing_item_count": 0,
        }
    return {
        "status": report.status,
        "summary": report.summary,
        "report_path": report.report_path or "",
        "planned_page_count": report.metrics.get("planned_page_count", 0),
        "handoff_item_count": report.metrics.get("handoff_item_count", 0),
        "ready_item_count": report.metrics.get("ready_item_count", 0),
        "missing_item_count": report.metrics.get("missing_item_count", 0),
    }


def _quality_review_summary(report: DesignQcReport | None) -> dict[str, Any]:
    """Build a concise QC summary for review metadata."""
    finding_counts = {"info": 0, "warning": 0, "error": 0}
    expert_finding_counts = {"info": 0, "warning": 0, "error": 0}
    if report is None:
        return {
            "status": "",
            "summary": "",
            "finding_counts": finding_counts,
            "expert": {
                "status": "",
                "finding_counts": expert_finding_counts,
                "error_count": 0,
                "warning_count": 0,
            },
            "attention_findings": [],
            "recommendations": [],
        }

    for finding in report.findings:
        finding_counts[finding.severity] = finding_counts.get(finding.severity, 0) + 1
    for severity, count in report.expert_finding_counts.items():
        expert_finding_counts[severity] = int(count)
    attention_findings = [
        {
            "finding_id": finding.finding_id,
            "severity": finding.severity,
            "category": finding.category,
            "target": finding.target,
            "summary": finding.summary,
        }
        for finding in report.findings
        if finding.severity in {"warning", "error"}
    ]
    recommendations: list[str] = []
    for finding in report.findings:
        if finding.recommendation and finding.recommendation not in recommendations:
            recommendations.append(finding.recommendation)
        if len(recommendations) >= 3:
            break
    return {
        "status": report.status,
        "summary": report.summary,
        "finding_counts": finding_counts,
        "expert": {
            "status": report.expert_status,
            "finding_counts": expert_finding_counts,
            "error_count": expert_finding_counts.get("error", 0),
            "warning_count": expert_finding_counts.get("warning", 0),
        },
        "attention_findings": attention_findings,
        "recommendations": recommendations,
    }


def _html_artifact_payload(state: DesignProductionState, artifact: HtmlArtifact) -> dict[str, Any]:
    payload = artifact.model_dump(mode="json")
    payload["source_refs"] = list(artifact.depends_on)
    payload["source_ref_details"] = source_ref_details(state, artifact.depends_on)
    return payload


def _preview_report_payload(state: DesignProductionState, report: PreviewReport) -> dict[str, Any]:
    payload = report.model_dump(mode="json")
    source_refs = preview_report_source_refs(state, report)
    payload["source_refs"] = source_refs
    payload["source_ref_details"] = source_ref_details(state, source_refs)
    return payload


def _workspace_file_payload(state: DesignProductionState, artifact: WorkspaceFileRef) -> dict[str, Any]:
    payload = artifact.model_dump(mode="json")
    source_refs = workspace_file_source_refs(state, artifact)
    payload["source_refs"] = source_refs
    payload["source_ref_details"] = source_ref_details(state, source_refs)
    return payload


def _brief_markdown(brief: DesignBrief | None) -> str:
    if brief is None:
        return "# Design Brief\n\nNo design brief has been prepared.\n"
    lines = [
        "# Design Brief",
        "",
        f"- Genre: {brief.design_genre}",
        f"- Goal: {brief.goal}",
        f"- Audience: {brief.audience}",
        f"- Primary action: {brief.primary_action}",
        f"- Confirmed: {brief.confirmed}",
        "",
        "## Selling Points",
        "",
    ]
    lines.extend(f"- {item}" for item in brief.selling_points)
    lines.extend(["", "## Constraints", ""])
    lines.extend(f"- {item}" for item in brief.constraints)
    return "\n".join(lines).rstrip() + "\n"


def _build_production_view(state: DesignProductionState, view_type: str) -> dict[str, Any]:
    if view_type == "overview":
        return _overview_view(state)
    if view_type == "brief":
        return _brief_view(state)
    if view_type == "design_system":
        return _design_system_view(state)
    if view_type == "design_system_extraction":
        return _design_system_extraction_view(state)
    if view_type == "components":
        return _components_view(state)
    if view_type == "accessibility":
        return _accessibility_view(state)
    if view_type == "layout":
        return _layout_view(state)
    if view_type == "preview":
        return _preview_view(state)
    if view_type == "diagnostics":
        return _diagnostics_view(state)
    if view_type == "lineage":
        return _lineage_view(state)
    if view_type == "pages":
        return _pages_view(state)
    if view_type == "quality":
        return _quality_view(state)
    if view_type == "events":
        return _events_view(state)
    if view_type == "artifacts":
        return _artifacts_view(state)
    raise ValueError(f"Unsupported view_type: {view_type}")


def _base_view(state: DesignProductionState, view_type: str) -> dict[str, Any]:
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


def _overview_view(state: DesignProductionState) -> dict[str, Any]:
    view = _base_view(state, "overview")
    view.update(
        {
            "design_genre": state.design_genre,
            "build_mode": state.build_mode,
            "active_review": (
                state.active_breakpoint.review_payload.model_dump(mode="json")
                if state.active_breakpoint is not None
                else None
            ),
            "counts": {
                "reference_assets": len(state.reference_assets),
                "html_artifacts": len(state.html_artifacts),
                "design_system_audit_reports": len(state.design_system_audit_reports),
                "component_inventory_reports": len(state.component_inventory_reports),
                "design_system_extraction_reports": len(state.design_system_extraction_reports),
                "accessibility_reports": len(state.accessibility_reports),
                "preview_reports": len(state.preview_reports),
                "pdf_export_reports": len(state.pdf_export_reports),
                "browser_diagnostics_reports": len(state.browser_diagnostics_reports),
                "artifact_lineage_reports": len(state.artifact_lineage_reports),
                "page_handoff_reports": len(state.page_handoff_reports),
                "qc_reports": len(state.qc_reports),
                "artifacts": len(state.artifacts),
                "events": len(state.production_events),
            },
            "artifacts": [_workspace_file_payload(state, item) for item in state.artifacts],
        }
    )
    return view


def _brief_view(state: DesignProductionState) -> dict[str, Any]:
    view = _base_view(state, "brief")
    view.update({"brief": state.brief.model_dump(mode="json") if state.brief is not None else None, "brief_path": f"{state.production_session.root_dir}/brief.md"})
    return view


def _design_system_view(state: DesignProductionState) -> dict[str, Any]:
    view = _base_view(state, "design_system")
    view.update(
        {
            "design_system": state.design_system.model_dump(mode="json") if state.design_system is not None else None,
            "design_system_path": f"{state.production_session.root_dir}/design_system.json",
            "design_system_audit_reports": [item.model_dump(mode="json") for item in state.design_system_audit_reports],
            "design_system_audit_report_path": f"{state.production_session.root_dir}/reports/design_system_audit.md",
            "design_system_extraction_reports": [item.model_dump(mode="json") for item in state.design_system_extraction_reports],
            "design_system_extraction_report_path": f"{state.production_session.root_dir}/reports/design_system_extraction.md",
        }
    )
    return view


def _design_system_extraction_view(state: DesignProductionState) -> dict[str, Any]:
    view = _base_view(state, "design_system_extraction")
    view.update(
        {
            "design_system_extraction_reports": [item.model_dump(mode="json") for item in state.design_system_extraction_reports],
            "latest_design_system_extraction_report": (
                state.design_system_extraction_reports[-1].model_dump(mode="json")
                if state.design_system_extraction_reports
                else None
            ),
            "design_system_extraction_report_path": f"{state.production_session.root_dir}/reports/design_system_extraction.md",
            "design_system_extraction_json_path": f"{state.production_session.root_dir}/reports/design_system_extraction.json",
        }
    )
    return view


def _components_view(state: DesignProductionState) -> dict[str, Any]:
    view = _base_view(state, "components")
    view.update(
        {
            "component_inventory_reports": [item.model_dump(mode="json") for item in state.component_inventory_reports],
            "component_inventory_report_path": f"{state.production_session.root_dir}/reports/component_inventory.md",
            "component_inventory_json_path": f"{state.production_session.root_dir}/reports/component_inventory.json",
        }
    )
    return view


def _accessibility_view(state: DesignProductionState) -> dict[str, Any]:
    view = _base_view(state, "accessibility")
    view.update(
        {
            "accessibility_reports": [item.model_dump(mode="json") for item in state.accessibility_reports],
            "latest_accessibility_report": (
                state.accessibility_reports[-1].model_dump(mode="json")
                if state.accessibility_reports
                else None
            ),
            "accessibility_report_path": f"{state.production_session.root_dir}/reports/accessibility_report.md",
            "accessibility_json_path": f"{state.production_session.root_dir}/reports/accessibility_report.json",
        }
    )
    return view


def _layout_view(state: DesignProductionState) -> dict[str, Any]:
    view = _base_view(state, "layout")
    view.update(
        {
            "layout_plan": state.layout_plan.model_dump(mode="json") if state.layout_plan is not None else None,
            "layout_plan_path": f"{state.production_session.root_dir}/layout_plan.json",
        }
    )
    return view


def _preview_view(state: DesignProductionState) -> dict[str, Any]:
    view = _base_view(state, "preview")
    view.update(
        {
            "html_artifacts": [_html_artifact_payload(state, item) for item in state.html_artifacts],
            "preview_reports": [_preview_report_payload(state, item) for item in state.preview_reports],
            "preview_report_path": f"{state.production_session.root_dir}/reports/preview_report.json",
            "design_system_extraction_reports": [item.model_dump(mode="json") for item in state.design_system_extraction_reports],
            "design_system_extraction_report_path": f"{state.production_session.root_dir}/reports/design_system_extraction.md",
            "accessibility_reports": [item.model_dump(mode="json") for item in state.accessibility_reports],
            "accessibility_report_path": f"{state.production_session.root_dir}/reports/accessibility_report.md",
            "browser_diagnostics_reports": [item.model_dump(mode="json") for item in state.browser_diagnostics_reports],
            "browser_diagnostics_report_path": f"{state.production_session.root_dir}/reports/browser_diagnostics.md",
        }
    )
    return view


def _diagnostics_view(state: DesignProductionState) -> dict[str, Any]:
    view = _base_view(state, "diagnostics")
    view.update(
        {
            "browser_diagnostics_reports": [item.model_dump(mode="json") for item in state.browser_diagnostics_reports],
            "latest_browser_diagnostics": (
                state.browser_diagnostics_reports[-1].model_dump(mode="json")
                if state.browser_diagnostics_reports
                else None
            ),
            "browser_diagnostics_report_path": f"{state.production_session.root_dir}/reports/browser_diagnostics.md",
            "browser_diagnostics_json_path": f"{state.production_session.root_dir}/reports/browser_diagnostics.json",
            "preview_reports": [_preview_report_payload(state, item) for item in state.preview_reports],
            "pdf_export_reports": [item.model_dump(mode="json") for item in state.pdf_export_reports],
        }
    )
    return view


def _lineage_view(state: DesignProductionState) -> dict[str, Any]:
    view = _base_view(state, "lineage")
    view.update(
        {
            "artifact_lineage_reports": [item.model_dump(mode="json") for item in state.artifact_lineage_reports],
            "latest_artifact_lineage": (
                state.artifact_lineage_reports[-1].model_dump(mode="json")
                if state.artifact_lineage_reports
                else None
            ),
            "artifact_lineage_report_path": f"{state.production_session.root_dir}/reports/artifact_lineage.md",
            "artifact_lineage_json_path": f"{state.production_session.root_dir}/reports/artifact_lineage.json",
            "html_artifacts": [_html_artifact_payload(state, item) for item in state.html_artifacts],
            "revision_history": state.revision_history,
        }
    )
    return view


def _pages_view(state: DesignProductionState) -> dict[str, Any]:
    view = _base_view(state, "pages")
    view.update(
        {
            "layout_plan": state.layout_plan.model_dump(mode="json") if state.layout_plan is not None else None,
            "page_handoff_reports": [item.model_dump(mode="json") for item in state.page_handoff_reports],
            "latest_page_handoff": (
                state.page_handoff_reports[-1].model_dump(mode="json")
                if state.page_handoff_reports
                else None
            ),
            "page_handoff_report_path": f"{state.production_session.root_dir}/reports/page_handoff.md",
            "page_handoff_json_path": f"{state.production_session.root_dir}/reports/page_handoff.json",
            "html_artifacts": [_html_artifact_payload(state, item) for item in state.html_artifacts],
        }
    )
    return view


def _quality_view(state: DesignProductionState) -> dict[str, Any]:
    view = _base_view(state, "quality")
    view.update(
        {
            "html_validation_reports": [item.model_dump(mode="json") for item in state.html_validation_reports],
            "design_system_audit_reports": [item.model_dump(mode="json") for item in state.design_system_audit_reports],
            "component_inventory_reports": [item.model_dump(mode="json") for item in state.component_inventory_reports],
            "design_system_extraction_reports": [item.model_dump(mode="json") for item in state.design_system_extraction_reports],
            "accessibility_reports": [item.model_dump(mode="json") for item in state.accessibility_reports],
            "pdf_export_reports": [item.model_dump(mode="json") for item in state.pdf_export_reports],
            "browser_diagnostics_reports": [item.model_dump(mode="json") for item in state.browser_diagnostics_reports],
            "artifact_lineage_reports": [item.model_dump(mode="json") for item in state.artifact_lineage_reports],
            "page_handoff_reports": [item.model_dump(mode="json") for item in state.page_handoff_reports],
            "qc_reports": [item.model_dump(mode="json") for item in state.qc_reports],
            "html_validation_report_path": f"{state.production_session.root_dir}/reports/html_validation.json",
            "design_system_audit_report_path": f"{state.production_session.root_dir}/reports/design_system_audit.md",
            "component_inventory_report_path": f"{state.production_session.root_dir}/reports/component_inventory.md",
            "design_system_extraction_report_path": f"{state.production_session.root_dir}/reports/design_system_extraction.md",
            "accessibility_report_path": f"{state.production_session.root_dir}/reports/accessibility_report.md",
            "pdf_export_report_path": f"{state.production_session.root_dir}/reports/pdf_export_report.json",
            "browser_diagnostics_report_path": f"{state.production_session.root_dir}/reports/browser_diagnostics.md",
            "artifact_lineage_report_path": f"{state.production_session.root_dir}/reports/artifact_lineage.md",
            "page_handoff_report_path": f"{state.production_session.root_dir}/reports/page_handoff.md",
            "qc_report_path": f"{state.production_session.root_dir}/reports/qc_report.md",
        }
    )
    return view


def _events_view(state: DesignProductionState) -> dict[str, Any]:
    view = _base_view(state, "events")
    view.update({"events": [item.model_dump(mode="json") for item in state.production_events]})
    return view


def _artifacts_view(state: DesignProductionState) -> dict[str, Any]:
    view = _base_view(state, "artifacts")
    view.update(
        {
            "artifacts": [_workspace_file_payload(state, item) for item in state.artifacts],
            "html_artifacts": [_html_artifact_payload(state, item) for item in state.html_artifacts],
            "reference_assets": [item.model_dump(mode="json") for item in state.reference_assets],
            "component_inventory_reports": [item.model_dump(mode="json") for item in state.component_inventory_reports],
            "design_system_extraction_reports": [item.model_dump(mode="json") for item in state.design_system_extraction_reports],
            "accessibility_reports": [item.model_dump(mode="json") for item in state.accessibility_reports],
            "pdf_export_reports": [item.model_dump(mode="json") for item in state.pdf_export_reports],
            "browser_diagnostics_reports": [item.model_dump(mode="json") for item in state.browser_diagnostics_reports],
            "artifact_lineage_reports": [item.model_dump(mode="json") for item in state.artifact_lineage_reports],
            "page_handoff_reports": [item.model_dump(mode="json") for item in state.page_handoff_reports],
            "export_artifacts": [_workspace_file_payload(state, item) for item in state.export_artifacts],
        }
    )
    return view
