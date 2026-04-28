"""Internal ADK-backed structured experts for Design production."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, TypeVar

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai.types import Content, Part
from pydantic import BaseModel, ConfigDict, Field

from conf.llm import build_llm, resolve_llm_model_name
from src.production.design.models import (
    DesignBrief,
    DesignGenre,
    DesignQcReport,
    DesignSystemSpec,
    DesignTokenColor,
    DesignTokenTypography,
    HtmlArtifact,
    HtmlValidationReport,
    LayoutPlan,
    LayoutSection,
    PageBlueprint,
    PreviewReport,
    ReferenceAssetEntry,
)
from src.production.design.prompt_catalog import render_prompt_template


class DesignDirectionPlan(BaseModel):
    """Structured output produced before the first Design review."""

    brief: DesignBrief
    design_system: DesignSystemSpec
    layout_plan: LayoutPlan
    notes: str = ""


class HtmlBuildOutput(BaseModel):
    """Structured output produced by the baseline HTML builder expert."""

    title: str = ""
    html: str = Field(description="Complete standalone HTML document.")
    section_fragments: dict[str, str] = Field(default_factory=dict)
    notes: str = ""


class _StrictModel(BaseModel):
    """Base class for ADK-facing structured outputs accepted by strict JSON schema backends."""

    model_config = ConfigDict(extra="forbid")


class _AdkNamedValue(_StrictModel):
    """One named scalar value in a strict ADK output schema."""

    name: str = ""
    value: str = ""


class _AdkDesignTokenColor(_StrictModel):
    """Strict color token returned by DesignSystemExpert."""

    name: str = ""
    value: str = ""
    usage: str = ""

    def to_design_token(self) -> DesignTokenColor:
        """Convert into the production color token model."""
        return DesignTokenColor(name=self.name, value=self.value, usage=self.usage)


class _AdkDesignTokenTypography(_StrictModel):
    """Strict typography token returned by DesignSystemExpert."""

    role: str = ""
    font_family: str = ""
    font_size_px: int | None = None
    font_weight: str = ""
    line_height: str = ""

    def to_design_token(self) -> DesignTokenTypography:
        """Convert into the production typography token model."""
        return DesignTokenTypography(
            role=self.role,
            font_family=self.font_family,
            font_size_px=self.font_size_px,
            font_weight=self.font_weight,
            line_height=self.line_height,
        )


class _AdkComponentToken(_StrictModel):
    """Strict component token group returned by DesignSystemExpert."""

    name: str = ""
    tokens: list[_AdkNamedValue] = Field(default_factory=list)


class _AdkDesignSystemSpec(_StrictModel):
    """Strict Design system output that avoids free-form object maps."""

    version: int = 1
    source: str = "generated"
    colors: list[_AdkDesignTokenColor] = Field(default_factory=list)
    typography: list[_AdkDesignTokenTypography] = Field(default_factory=list)
    spacing: list[_AdkNamedValue] = Field(default_factory=list)
    radii: list[_AdkNamedValue] = Field(default_factory=list)
    shadows: list[_AdkNamedValue] = Field(default_factory=list)
    component_tokens: list[_AdkComponentToken] = Field(default_factory=list)
    notes: str = ""

    def to_design_system_spec(self) -> DesignSystemSpec:
        """Convert strict ADK output into the production Design system model."""
        return DesignSystemSpec(
            version=self.version,
            source=_coerce_design_system_source(self.source),
            colors=[token.to_design_token() for token in self.colors if token.name and token.value],
            typography=[token.to_design_token() for token in self.typography if token.role and token.font_family],
            spacing=_named_values_to_dict(self.spacing),
            radii=_named_values_to_dict(self.radii),
            shadows=_named_values_to_dict(self.shadows),
            component_tokens=_component_tokens_to_dict(self.component_tokens),
            notes=self.notes,
        )


class _AdkLayoutSection(_StrictModel):
    """Strict layout section returned by LayoutPlannerExpert."""

    section_id: str = ""
    title: str = ""
    purpose: str = ""
    content: list[str] = Field(default_factory=list)
    required_asset_ids: list[str] = Field(default_factory=list)
    missing_asset_briefs: list[str] = Field(default_factory=list)
    responsive_notes: str = ""
    expert_hints: list[_AdkNamedValue] = Field(default_factory=list)

    def to_layout_section(self) -> LayoutSection:
        """Convert into the production layout section model."""
        kwargs: dict[str, Any] = {
            "title": self.title or "Section",
            "purpose": self.purpose,
            "content": self.content,
            "required_asset_ids": self.required_asset_ids,
            "missing_asset_briefs": self.missing_asset_briefs,
            "responsive_notes": self.responsive_notes,
            "expert_hints": _named_values_to_dict(self.expert_hints),
        }
        if self.section_id:
            kwargs["section_id"] = self.section_id
        return LayoutSection(**kwargs)


class _AdkPageBlueprint(_StrictModel):
    """Strict page blueprint returned by LayoutPlannerExpert."""

    page_id: str = ""
    title: str = ""
    path: str = "index.html"
    sections: list[_AdkLayoutSection] = Field(default_factory=list)
    device_targets: list[str] = Field(default_factory=lambda: ["desktop", "mobile"])
    version: int = 1
    status: str = "draft"

    def to_page_blueprint(self) -> PageBlueprint:
        """Convert into the production page blueprint model."""
        kwargs: dict[str, Any] = {
            "title": self.title or "Page",
            "path": self.path or "index.html",
            "sections": [section.to_layout_section() for section in self.sections],
            "device_targets": self.device_targets or ["desktop", "mobile"],
            "version": self.version,
            "status": _coerce_page_status(self.status),
        }
        if self.page_id:
            kwargs["page_id"] = self.page_id
        return PageBlueprint(**kwargs)


class _AdkLayoutPlan(_StrictModel):
    """Strict layout plan output that avoids free-form section hint maps."""

    version: int = 1
    pages: list[_AdkPageBlueprint] = Field(default_factory=list)
    global_notes: str = ""

    def to_layout_plan(self) -> LayoutPlan:
        """Convert into the production layout plan model."""
        return LayoutPlan(
            version=self.version,
            pages=[page.to_page_blueprint() for page in self.pages],
            global_notes=self.global_notes,
        )


class _AdkSectionFragment(_StrictModel):
    """Strict HTML section fragment entry returned by HtmlBuilderExpert."""

    section_id: str = ""
    html: str = ""


class _AdkHtmlBuildOutput(_StrictModel):
    """Strict HTML builder output that avoids free-form fragment maps."""

    title: str = ""
    html: str = Field(description="Complete standalone HTML document.")
    section_fragments: list[_AdkSectionFragment] = Field(default_factory=list)
    notes: str = ""

    def to_html_build_output(self) -> HtmlBuildOutput:
        """Convert into the production HTML build output model."""
        return HtmlBuildOutput(
            title=self.title,
            html=self.html,
            section_fragments={
                fragment.section_id: fragment.html
                for fragment in self.section_fragments
                if fragment.section_id and fragment.html
            },
            notes=self.notes,
        )


SchemaT = TypeVar("SchemaT", bound=BaseModel)


class DesignExpertRuntime:
    """Run internal Design experts through ADK structured-output agents."""

    def __init__(
        self,
        *,
        model_reference: str | None = None,
        app_name: str = "creative_claw_design_internal",
    ) -> None:
        """Initialize the internal Design expert runtime."""
        self.model_reference = model_reference
        self.app_name = app_name

    async def plan_direction(
        self,
        *,
        user_prompt: str,
        design_genre: DesignGenre,
        design_settings: dict[str, Any],
        reference_assets: list[ReferenceAssetEntry],
    ) -> DesignDirectionPlan:
        """Generate brief, design system, and layout plan for the first review."""
        playbook_text = load_design_playbook(design_genre)
        reference_assets_json = _json_dump([asset.model_dump(mode="json") for asset in reference_assets])

        brief_prompt = render_prompt_template(
            "brief_expert",
            {
                "design_genre": design_genre,
                "user_prompt": user_prompt,
                "design_settings_json": _json_dump(design_settings),
                "reference_assets_json": reference_assets_json,
                "playbook_text": playbook_text,
            },
        )
        brief = await self._run_structured_agent(
            agent_name="DesignBriefExpert",
            instruction="You create concise, reviewable Design briefs for HTML-centered production.",
            request_text=brief_prompt,
            output_schema=DesignBrief,
            output_key="design_brief",
        )
        brief.design_genre = design_genre

        design_system_prompt = render_prompt_template(
            "design_system_expert",
            {
                "brief_json": brief.model_dump_json(indent=2),
                "reference_assets_json": reference_assets_json,
                "playbook_text": playbook_text,
            },
        )
        adk_design_system = await self._run_structured_agent(
            agent_name="DesignSystemExpert",
            instruction="You create practical design systems for responsive HTML artifacts.",
            request_text=design_system_prompt,
            output_schema=_AdkDesignSystemSpec,
            output_key="design_system",
        )
        design_system = adk_design_system.to_design_system_spec()
        if design_system.source == "placeholder":
            design_system.source = "generated"

        layout_prompt = render_prompt_template(
            "layout_planner_expert",
            {
                "brief_json": brief.model_dump_json(indent=2),
                "design_system_json": design_system.model_dump_json(indent=2),
                "design_settings_json": _json_dump(design_settings),
                "requested_build_mode": _requested_layout_build_mode(design_settings),
                "requested_pages_json": _json_dump(_requested_page_specs(design_settings)),
                "reference_assets_json": reference_assets_json,
                "playbook_text": playbook_text,
            },
        )
        adk_layout_plan = await self._run_structured_agent(
            agent_name="LayoutPlannerExpert",
            instruction="You create page-aware layout plans with stable HTML section ids.",
            request_text=layout_prompt,
            output_schema=_AdkLayoutPlan,
            output_key="layout_plan",
        )
        layout_plan = adk_layout_plan.to_layout_plan()

        return DesignDirectionPlan(
            brief=brief,
            design_system=design_system,
            layout_plan=layout_plan,
            notes="Generated by internal structured Design experts.",
        )

    async def build_html(
        self,
        *,
        brief: DesignBrief,
        design_system: DesignSystemSpec,
        layout_plan: LayoutPlan,
        reference_assets: list[ReferenceAssetEntry],
        build_mode: str = "baseline",
        revision_request: dict[str, Any] | None = None,
        revision_impact: dict[str, Any] | None = None,
        previous_html: str = "",
    ) -> HtmlBuildOutput:
        """Generate a baseline or revision HTML artifact for one target page."""
        prompt = render_prompt_template(
            "html_builder_expert",
            {
                "build_mode": build_mode,
                "brief_json": brief.model_dump_json(indent=2),
                "design_system_json": design_system.model_dump_json(indent=2),
                "layout_plan_json": layout_plan.model_dump_json(indent=2),
                "reference_assets_json": _json_dump([asset.model_dump(mode="json") for asset in reference_assets]),
                "revision_request_json": _json_dump(revision_request or {}),
                "revision_impact_json": _json_dump(revision_impact or {}),
                "previous_html_summary": _summarize_previous_html(previous_html),
            },
        )
        adk_output = await self._run_structured_agent(
            agent_name="HtmlBuilderExpert",
            instruction=(
                "You build complete, portable, responsive HTML for one target page. "
                "Return only fields requested by the schema."
            ),
            request_text=prompt,
            output_schema=_AdkHtmlBuildOutput,
            output_key="html_build_output",
        )
        output = adk_output.to_html_build_output()
        output.html = normalize_generated_html(output.html)
        return output

    async def assess_quality(
        self,
        *,
        brief: DesignBrief | None,
        design_system: DesignSystemSpec | None,
        layout_plan: LayoutPlan | None,
        artifact: HtmlArtifact,
        validation_report: HtmlValidationReport,
        preview_reports: list[PreviewReport],
        html: str,
    ) -> DesignQcReport:
        """Assess generated HTML quality with a structured Design QC expert."""
        prompt = render_prompt_template(
            "design_qc_expert",
            {
                "brief_json": _model_json_or_null(brief),
                "design_system_json": _model_json_or_null(design_system),
                "layout_plan_json": _model_json_or_null(layout_plan),
                "artifact_json": artifact.model_dump_json(indent=2),
                "validation_report_json": validation_report.model_dump_json(indent=2),
                "preview_reports_json": _json_dump([report.model_dump(mode="json") for report in preview_reports]),
                "html_summary": _summarize_html(html, max_chars=8000),
            },
        )
        report = await self._run_structured_agent(
            agent_name="DesignQCExpert",
            instruction=(
                "You assess generated HTML design quality. "
                "Use validator and preview facts as authoritative evidence."
            ),
            request_text=prompt,
            output_schema=DesignQcReport,
            output_key="design_qc_report",
        )
        report.artifact_ids = [artifact.artifact_id]
        if not report.summary:
            report.summary = "DesignQCExpert completed supplemental quality assessment."
        return report

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
        user_id = "design-production"
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


def normalize_generated_html(html_text: str) -> str:
    """Return complete HTML text without Markdown fences."""
    text = str(html_text or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    lowered = text.lower()
    if lowered.startswith("<html"):
        text = "<!doctype html>\n" + text
    if "<html" not in lowered or "</html>" not in lowered:
        raise ValueError("HtmlBuilderExpert returned incomplete HTML.")
    return text.rstrip() + "\n"


def load_design_playbook(design_genre: str) -> str:
    """Load the genre playbook text used by internal Design experts."""
    filename_by_genre = {
        "landing_page": "landing_page.md",
        "ui_design": "ui_design.md",
        "product_detail_page": "product_detail_page.md",
    }
    filename = filename_by_genre.get(str(design_genre), "landing_page.md")
    repo_root = Path(__file__).resolve().parents[3]
    playbook_path = repo_root / "playbooks" / "design" / filename
    try:
        return playbook_path.read_text(encoding="utf-8")
    except OSError:
        return ""


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


def _requested_layout_build_mode(design_settings: dict[str, Any]) -> str:
    """Return the requested layout build mode for prompt conditioning."""
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


def _requested_page_specs(design_settings: dict[str, Any]) -> list[dict[str, Any]]:
    """Return compact requested page specs for layout planning prompts."""
    raw_pages = design_settings.get("pages")
    if not isinstance(raw_pages, list):
        return []
    page_specs: list[dict[str, Any]] = []
    for page in raw_pages:
        if not isinstance(page, dict):
            continue
        page_specs.append(
            {
                "title": str(page.get("title") or "").strip(),
                "path": str(page.get("path") or "").strip(),
                "purpose": str(page.get("purpose") or page.get("description") or "").strip(),
                "sections": page.get("sections") if isinstance(page.get("sections"), list) else [],
            }
        )
    return page_specs


def _named_values_to_dict(items: list[_AdkNamedValue]) -> dict[str, str]:
    """Convert strict named-value lists into production token maps."""
    values: dict[str, str] = {}
    for item in items:
        name = str(item.name or "").strip()
        value = str(item.value or "").strip()
        if name and value:
            values[name] = value
    return values


def _component_tokens_to_dict(items: list[_AdkComponentToken]) -> dict[str, dict[str, str]]:
    """Convert strict component token groups into production component token maps."""
    values: dict[str, dict[str, str]] = {}
    for item in items:
        name = str(item.name or "").strip()
        if not name:
            continue
        tokens = _named_values_to_dict(item.tokens)
        if tokens:
            values[name] = tokens
    return values


def _coerce_design_system_source(value: str) -> str:
    """Return a production-safe DesignSystemSpec source literal."""
    source = str(value or "").strip()
    if source in {"generated", "extracted", "mixed", "placeholder"}:
        return source
    return "generated"


def _coerce_page_status(value: str) -> str:
    """Return a production-safe PageBlueprint status literal."""
    status = str(value or "").strip()
    if status in {"draft", "approved", "stale"}:
        return status
    return "draft"


def _model_json_or_null(value: BaseModel | None) -> str:
    """Dump a Pydantic model as JSON, or null when it is absent."""
    if value is None:
        return "null"
    return value.model_dump_json(indent=2)


def _summarize_previous_html(html_text: str, *, max_chars: int = 6000) -> str:
    """Compress previous HTML enough for revision prompts."""
    return _summarize_html(html_text, max_chars=max_chars)


def _summarize_html(html_text: str, *, max_chars: int) -> str:
    """Compress HTML into a prompt-sized single-line summary."""
    text = " ".join(str(html_text or "").split())
    if not text:
        return "(none)"
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()} ... [truncated]"
