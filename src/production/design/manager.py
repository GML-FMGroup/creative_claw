"""Runtime service for Design production."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.production.design.impact import build_revision_impact_view, normalize_revision_request
from src.production.design.models import (
    DesignBrief,
    DesignProductionState,
    DesignSystemSpec,
    DesignTokenColor,
    DesignTokenTypography,
    LayoutPlan,
    LayoutSection,
    PageBlueprint,
)
from src.production.design.placeholders import PlaceholderHtmlBuilder
from src.production.design.quality import build_quality_report, quality_report_markdown
from src.production.design.tools.asset_ingestor import AssetIngestor
from src.production.design.tools.html_validator import HtmlValidator
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


_VIEW_TYPES = ("overview", "brief", "design_system", "layout", "preview", "quality", "events", "artifacts")


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
        placeholder_builder: PlaceholderHtmlBuilder | None = None,
    ) -> None:
        """Initialize the Design production manager."""
        self.store = store or ProductionSessionStore()
        self.asset_ingestor = asset_ingestor or AssetIngestor()
        self.html_validator = html_validator or HtmlValidator()
        self.preview_renderer = preview_renderer or HtmlPreviewRenderer()
        self.placeholder_builder = placeholder_builder or PlaceholderHtmlBuilder()

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
            build_mode="single_html",
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
            _prepare_placeholder_planning_state(state, user_prompt=user_prompt, design_settings=design_settings or {})
            if not placeholder_design:
                return self._pause_for_design_direction_review(
                    state,
                    message="Design direction is ready for review before HTML generation.",
                    adk_state=adk_state,
                )
            return await self._build_and_complete_placeholder(state, adk_state=adk_state)
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
            state.revision_history.append(response)
            state.production_events.append(
                ProductionEvent(
                    event_type="design_direction_revised",
                    stage=state.stage,
                    message="User requested design revisions; returning to design direction review.",
                    metadata={"user_response": response},
                )
            )
            return self._pause_for_design_direction_review(
                state,
                message="Design revision notes were captured. Review the updated direction before rebuilding.",
                adk_state=adk_state,
            )
        if decision != "approve":
            return self._result_from_state(state, message="Please respond with decision=approve, revise, or cancel.")

        if state.active_breakpoint.stage == "design_direction_review":
            return await self._build_and_pause_for_preview_review(state, adk_state=adk_state)
        if state.active_breakpoint.stage == "preview_review":
            return self._approve_preview_and_complete(state, adk_state=adk_state)
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
        impact_view = build_revision_impact_view(state, revision_request)
        state.revision_history.append(revision_request)
        for artifact in state.html_artifacts:
            artifact.status = "stale"
            artifact.stale_reason = "Revision applied; P0 rebuilds the full page."
        if state.layout_plan is not None:
            for page in state.layout_plan.pages:
                page.status = "stale"
        state.production_events.append(
            ProductionEvent(
                event_type="revision_applied",
                stage=state.stage,
                message="Design revision was applied; returning to design direction review.",
                metadata={"impact": impact_view},
            )
        )
        return self._pause_for_design_direction_review(
            state,
            message="Revision applied. Review the updated direction before rebuilding the page.",
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

    async def _build_and_complete_placeholder(self, state: DesignProductionState, *, adk_state) -> ProductionRunResult:
        await self._build_html_validation_preview_and_qc(state)
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
        await self._build_html_validation_preview_and_qc(state)
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

    def _approve_preview_and_complete(self, state: DesignProductionState, *, adk_state) -> ProductionRunResult:
        for artifact in state.html_artifacts:
            if artifact.status == "draft":
                artifact.status = "approved"
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

    async def _build_html_validation_preview_and_qc(self, state: DesignProductionState) -> None:
        session_root = self.store.session_root(state.production_session)
        _ensure_design_dirs(session_root)
        state.stage = "html_building"
        state.progress_percent = max(state.progress_percent, 55)
        artifact = self.placeholder_builder.build(session_root=session_root, state=state)
        state.html_artifacts.append(artifact)

        state.stage = "html_validation"
        validation_report = self.html_validator.validate(
            artifact.path,
            session_root=session_root,
            artifact_id=artifact.artifact_id,
        )
        state.html_validation_reports.append(validation_report)
        if validation_report.status == "invalid":
            artifact.status = "failed"
            raise RuntimeError("; ".join(validation_report.issues) or "HTML validation failed")
        artifact.status = "valid"

        state.stage = "html_preview"
        preview_reports = await self.preview_renderer.render(
            artifact_id=artifact.artifact_id,
            html_path=artifact.path,
            output_dir=session_root / "previews",
        )
        state.preview_reports.extend(preview_reports)

        state.stage = "quality_check"
        qc_report = build_quality_report(
            artifact=artifact,
            validation_report=validation_report,
            preview_reports=preview_reports,
            brief=state.brief,
            layout_plan=state.layout_plan,
        )
        state.qc_reports.append(qc_report)
        state.progress_percent = max(state.progress_percent, 90)
        state.production_events.append(
            ProductionEvent(
                event_type="html_artifact_built",
                stage=state.stage,
                message="Built, validated, previewed, and checked one HTML artifact.",
                metadata={
                    "artifact_id": artifact.artifact_id,
                    "validation_status": validation_report.status,
                    "qc_status": qc_report.status,
                },
            )
        )

    def _finalize_artifacts(self, state: DesignProductionState) -> None:
        final_artifacts: list[WorkspaceFileRef] = []
        latest_html = state.html_artifacts[-1] if state.html_artifacts else None
        if latest_html is not None:
            final_artifacts.append(
                WorkspaceFileRef(
                    name=Path(latest_html.path).name,
                    path=latest_html.path,
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
            final_artifacts.append(
                WorkspaceFileRef(
                    name="qc_report.md",
                    path=qc_path,
                    description="Design quality report.",
                    source=self.capability,
                )
            )
        state.artifacts = final_artifacts

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
    for child_name in ("artifacts", "previews", "reports"):
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
        content_requirements=["Single-file HTML", "Responsive desktop and mobile layout", "Stable section ids"],
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
    state.layout_plan = _placeholder_layout_plan(genre=genre, brief=state.brief)
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
    return [cleaned[:80], "Reviewable production state", "Portable single-file HTML"]


def _placeholder_layout_plan(*, genre: str, brief: DesignBrief) -> LayoutPlan:
    section_titles = {
        "ui_design": ["Top Filters", "KPI Overview", "Operational Detail", "Alerts and Next Actions"],
        "product_detail_page": ["Product Hero", "Core Benefits", "Scenario Details", "Final Contact CTA"],
        "landing_page": ["Hero", "Value Proposition", "Feature System", "Final CTA"],
    }.get(genre, ["Hero", "Content", "Proof", "CTA"])
    sections = [
        LayoutSection(
            section_id=_stable_section_id(title),
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
    page = PageBlueprint(
        title=_page_title(genre),
        sections=sections,
        device_targets=brief.device_targets,
    )
    return LayoutPlan(pages=[page], global_notes="P0a single-page placeholder layout.")


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
            {"kind": "html_artifacts", "artifacts": [item.model_dump(mode="json") for item in state.html_artifacts]},
            {"kind": "preview_reports", "reports": [item.model_dump(mode="json") for item in state.preview_reports]},
            {"kind": "qc_reports", "reports": [item.model_dump(mode="json") for item in state.qc_reports]},
        ],
        options=[
            {"id": "approve", "label": "Approve final design"},
            {"id": "revise", "label": "Request changes"},
            {"id": "cancel", "label": "Cancel"},
        ],
    )


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
    if view_type == "layout":
        return _layout_view(state)
    if view_type == "preview":
        return _preview_view(state)
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
                "preview_reports": len(state.preview_reports),
                "qc_reports": len(state.qc_reports),
                "artifacts": len(state.artifacts),
                "events": len(state.production_events),
            },
            "artifacts": [item.model_dump(mode="json") for item in state.artifacts],
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
            "html_artifacts": [item.model_dump(mode="json") for item in state.html_artifacts],
            "preview_reports": [item.model_dump(mode="json") for item in state.preview_reports],
            "preview_report_path": f"{state.production_session.root_dir}/reports/preview_report.json",
        }
    )
    return view


def _quality_view(state: DesignProductionState) -> dict[str, Any]:
    view = _base_view(state, "quality")
    view.update(
        {
            "html_validation_reports": [item.model_dump(mode="json") for item in state.html_validation_reports],
            "qc_reports": [item.model_dump(mode="json") for item in state.qc_reports],
            "html_validation_report_path": f"{state.production_session.root_dir}/reports/html_validation.json",
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
            "artifacts": [item.model_dump(mode="json") for item in state.artifacts],
            "html_artifacts": [item.model_dump(mode="json") for item in state.html_artifacts],
            "reference_assets": [item.model_dump(mode="json") for item in state.reference_assets],
            "export_artifacts": [item.model_dump(mode="json") for item in state.export_artifacts],
        }
    )
    return view
