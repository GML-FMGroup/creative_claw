import asyncio
import json
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.production.design.accessibility import build_accessibility_report
from src.production.design.component_inventory import build_component_inventory
from src.production.design.design_system_audit import audit_design_system
from src.production.design.design_system_extractor import build_design_system_extraction
from src.production.design.expert_runtime import (
    DesignDirectionPlan,
    DesignExpertRuntime,
    HtmlBuildOutput,
    _AdkComponentToken,
    _AdkDesignSystemSpec,
    _AdkDesignTokenColor,
    _AdkDesignTokenTypography,
    _AdkHtmlBuildOutput,
    _AdkLayoutPlan,
    _AdkLayoutSection,
    _AdkNamedValue,
    _AdkPageBlueprint,
    _AdkSectionFragment,
)
from src.production.design.manager import DesignProductionManager
from src.production.design.models import (
    DesignBrief,
    DesignProductionState,
    DesignQcFinding,
    DesignQcReport,
    DesignSystemSpec,
    DesignTokenColor,
    DesignTokenTypography,
    HtmlArtifact,
    HtmlValidationReport,
    LayoutPlan,
    LayoutSection,
    PageBlueprint,
    PdfExportReport,
    PreviewReport,
)
from src.production.design.page_handoff import build_page_handoff
from src.production.design.prompt_catalog import (
    DesignPromptCatalogError,
    available_prompt_templates,
    render_prompt_template,
)
from src.production.design.tool import run_design_production
from src.production.design.tools.html_validator import HtmlValidator
from src.production.models import ProductionSession, utc_now_iso
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path, workspace_root


def _adk_state(sid: str = "session_design_test") -> dict:
    return {
        "sid": sid,
        "turn_index": 1,
        "step": 0,
        "channel": "cli",
        "chat_id": "terminal",
        "sender_id": "cli-user",
        "uploaded": [],
        "generated": [],
        "files_history": [],
        "final_file_paths": [],
    }


class _FakePreviewRenderer:
    async def render(self, *, artifact_id: str, html_path, output_dir: Path, viewports=None):
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(str(html_path)).stem
        desktop_path = output_dir / f"{stem}_{artifact_id}_desktop.png"
        mobile_path = output_dir / f"{stem}_{artifact_id}_mobile.png"
        desktop_path.write_bytes(b"fake-desktop-preview")
        mobile_path.write_bytes(b"fake-mobile-preview")
        return [
            PreviewReport(
                artifact_id=artifact_id,
                viewport="desktop",
                screenshot_path=workspace_relative_path(desktop_path),
                layout_metrics={"viewportWidth": 1440, "bodyScrollWidth": 1120, "bodyScrollHeight": 900},
            ),
            PreviewReport(
                artifact_id=artifact_id,
                viewport="mobile",
                screenshot_path=workspace_relative_path(mobile_path),
                layout_metrics={"viewportWidth": 390, "bodyScrollWidth": 390, "bodyScrollHeight": 1200},
            ),
        ]


class _UnavailablePreviewRenderer:
    async def render(self, *, artifact_id: str, html_path, output_dir: Path, viewports=None):
        return [
            PreviewReport(
                artifact_id=artifact_id,
                viewport="desktop",
                valid=False,
                issues=[
                    "Browser environment is unavailable for preview rendering: "
                    "Playwright browser executable is not installed."
                ],
                layout_metrics={
                    "preview": "unavailable",
                    "browser_environment": "runtime_unavailable",
                    "remediation": "Install Playwright Chromium browser support with "
                    "`python -m playwright install chromium`, then rerun Design preview or PDF export.",
                },
            ),
            PreviewReport(
                artifact_id=artifact_id,
                viewport="mobile",
                valid=False,
                issues=[
                    "Browser environment is unavailable for preview rendering: "
                    "Playwright browser executable is not installed."
                ],
                layout_metrics={
                    "preview": "unavailable",
                    "browser_environment": "runtime_unavailable",
                    "remediation": "Install Playwright Chromium browser support with "
                    "`python -m playwright install chromium`, then rerun Design preview or PDF export.",
                },
            ),
        ]


class _FakePdfExporter:
    async def export(self, *, artifact_id: str, html_path, output_path: Path) -> PdfExportReport:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"%PDF-1.4\n% fake design pdf\n")
        return PdfExportReport(
            artifact_id=artifact_id,
            source_html_path=str(html_path),
            pdf_path=workspace_relative_path(output_path),
            status="exported",
        )


class _UnavailablePdfExporter:
    async def export(self, *, artifact_id: str, html_path, output_path: Path) -> PdfExportReport:
        return PdfExportReport(
            artifact_id=artifact_id,
            source_html_path=str(html_path),
            status="unavailable",
            issues=["Playwright is not available: ImportError"],
        )


class _FakeDesignExpertRuntime:
    def __init__(self, *, fail_quality: bool = False) -> None:
        self.build_calls = []
        self.quality_calls = []
        self.fail_quality = fail_quality

    @property
    def model_name(self) -> str:
        return "fake/design-model"

    async def plan_direction(self, *, user_prompt, design_genre, design_settings, reference_assets):
        page_title = "Expert Product Detail Page" if design_genre == "product_detail_page" else "Expert Dashboard Design"
        brief = DesignBrief(
            design_genre=design_genre,
            goal=user_prompt,
            audience="operations teams",
            primary_action="Review dashboard",
            selling_points=["Fast operational scanning", "Clear exception handling"],
            content_requirements=["Single-file HTML", "Responsive layout"],
            constraints=["No local absolute paths"],
        )
        design_system = DesignSystemSpec(
            source="generated",
            colors=[
                DesignTokenColor(name="primary", value="#0F766E", usage="Primary actions"),
                DesignTokenColor(name="accent", value="#EAB308", usage="Highlights"),
                DesignTokenColor(name="ink", value="#111827", usage="Text"),
                DesignTokenColor(name="surface", value="#F8FAFC", usage="Background"),
            ],
            typography=[
                DesignTokenTypography(
                    role="display",
                    font_family="Inter, system-ui, sans-serif",
                    font_size_px=44,
                    font_weight="760",
                    line_height="1.08",
                )
            ],
            spacing={"section_y": "28px"},
            radii={"default": "8px"},
        )
        pages = [
            PageBlueprint(
                title=page_title,
                path="index.html",
                sections=[
                    LayoutSection(
                        section_id="hero",
                        title="Hero",
                        purpose="Introduce the dashboard value.",
                        content=["Monitor GMV, inventory, and alerts in one place."],
                        responsive_notes="Stack summary metrics on mobile.",
                    ),
                    LayoutSection(
                        section_id="workflow",
                        title="Workflow",
                        purpose="Show the operating loop.",
                        content=["Prioritize exceptions and route next actions."],
                        responsive_notes="Use a single-column timeline on mobile.",
                    ),
                ],
                device_targets=["desktop", "mobile"],
            )
        ]
        if design_settings.get("build_mode") == "multi_html":
            pages = [
                PageBlueprint(
                    title=str(raw_page.get("title") or f"Page {index}"),
                    path=str(raw_page.get("path") or f"page-{index}.html"),
                    sections=[
                        LayoutSection(
                            section_id=f"page-{index}-hero",
                            title="Hero",
                            purpose="Introduce the page value.",
                            content=[f"Page-specific content for page {index}."],
                            responsive_notes="Stack content on mobile.",
                        )
                    ],
                    device_targets=["desktop", "mobile"],
                )
                for index, raw_page in enumerate(design_settings.get("pages") or [], start=1)
                if isinstance(raw_page, dict)
            ] or pages
        layout_plan = LayoutPlan(pages=pages, global_notes="Fake runtime layout.")
        return DesignDirectionPlan(
            brief=brief,
            design_system=design_system,
            layout_plan=layout_plan,
            notes="fake direction",
        )

    async def build_html(
        self,
        *,
        brief,
        design_system,
        layout_plan,
        reference_assets,
        build_mode="baseline",
        revision_request=None,
        revision_impact=None,
        previous_html="",
    ):
        self.build_calls.append(
            {
                "build_mode": build_mode,
                "revision_request": revision_request or {},
                "revision_impact": revision_impact or {},
                "previous_html": previous_html,
                "page_ids": [page.page_id for page in layout_plan.pages],
            }
        )
        revised = build_mode == "revision"
        heading = "Expert revised HTML design" if revised else "Expert generated HTML design"
        body = "Revision applied: make the hero more product-led." if revised else "Monitor GMV, inventory, and alerts in one place."
        return HtmlBuildOutput(
            title="Expert Revised Dashboard Design" if revised else "Expert Dashboard Design",
            html=f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{"Expert Revised Dashboard Design" if revised else "Expert Dashboard Design"}</title>
  <style>
    body {{ margin: 0; font-family: Inter, system-ui, sans-serif; color: #111827; background: #f8fafc; }}
    main {{ width: min(1040px, calc(100% - 32px)); margin: 0 auto; padding: 32px 0; }}
    section {{ background: #fff; border: 1px solid #dbe3ef; border-radius: 8px; padding: 24px; margin: 16px 0; }}
    h1 {{ font-size: 44px; line-height: 1.08; margin: 0; }}
    @media (max-width: 720px) {{ h1 {{ font-size: 34px; }} section {{ padding: 18px; }} }}
  </style>
</head>
<body>
  <main>
    <section id="hero"><h1>{heading}</h1><p>{body}</p></section>
    <section id="workflow"><h2>Workflow</h2><p>Prioritize exceptions and route next actions.</p></section>
  </main>
</body>
</html>
""",
            section_fragments={"hero": "Hero", "workflow": "Workflow"},
            notes="fake html",
        )

    async def assess_quality(
        self,
        *,
        brief,
        design_system,
        layout_plan,
        artifact,
        validation_report,
        preview_reports,
        html,
    ):
        self.quality_calls.append(
            {
                "artifact_id": artifact.artifact_id,
                "validation_status": validation_report.status,
                "preview_count": len(preview_reports),
                "html": html,
            }
        )
        if self.fail_quality:
            raise RuntimeError("fake qc unavailable")
        return DesignQcReport(
            artifact_ids=[artifact.artifact_id],
            status="pass",
            summary="Fake expert QC passed.",
            findings=[
                DesignQcFinding(
                    severity="info",
                    category="brief_fit",
                    target="hero",
                    summary="Hero reflects the requested operational dashboard brief.",
                    recommendation="Keep the primary dashboard value visible above the fold.",
                )
            ],
        )


class _CapturingDesignExpertRuntime(DesignExpertRuntime):
    def __init__(self) -> None:
        super().__init__(model_reference="fake/design-model")
        self.requests = []

    async def _run_structured_agent(self, *, agent_name, instruction, request_text, output_schema, output_key):
        self.requests.append(
            {
                "agent_name": agent_name,
                "instruction": instruction,
                "request_text": request_text,
                "output_key": output_key,
            }
        )
        if output_schema is DesignBrief:
            return DesignBrief(
                design_genre="landing_page",
                goal="Design a multi-page microsite",
                audience="buyers",
                primary_action="Explore product",
            )
        if output_schema is _AdkDesignSystemSpec:
            return _AdkDesignSystemSpec(source="generated")
        if output_schema is _AdkLayoutPlan:
            return _AdkLayoutPlan(
                pages=[
                    _AdkPageBlueprint(
                        title="Home",
                        path="index.html",
                        sections=[
                            _AdkLayoutSection(
                                section_id="home-hero",
                                title="Hero",
                                purpose="Introduce the product.",
                            )
                        ],
                    ),
                    _AdkPageBlueprint(
                        title="Product",
                        path="product.html",
                        sections=[
                            _AdkLayoutSection(
                                section_id="product-overview",
                                title="Product Overview",
                                purpose="Explain product value.",
                            )
                        ],
                    ),
                ],
                global_notes="Captured multi-page plan.",
            )
        raise AssertionError(f"Unexpected schema for capture runtime: {output_schema!r}")


class DesignProductionTests(unittest.TestCase):
    def test_manager_start_placeholder_completes_and_projects_artifacts(self) -> None:
        state = _adk_state("session_design_placeholder")
        manager = DesignProductionManager(preview_renderer=_FakePreviewRenderer())

        result = asyncio.run(
            manager.start(
                user_prompt="Design a SaaS landing page for an AI support platform",
                input_files=[],
                placeholder_design=True,
                design_settings=None,
                adk_state=state,
            )
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.stage, "completed")
        self.assertEqual(result.capability, "design")
        self.assertGreaterEqual(len(result.artifacts), 3)
        artifact_paths = {artifact.path for artifact in result.artifacts}
        html_paths = [path for path in artifact_paths if path.endswith(".html")]
        self.assertEqual(len(html_paths), 1)
        self.assertTrue(resolve_workspace_path(html_paths[0]).exists())
        artifact_names = {artifact.name for artifact in result.artifacts}
        self.assertIn("design_system_audit.md", artifact_names)
        self.assertIn("component_inventory.md", artifact_names)
        self.assertIn("component_inventory.json", artifact_names)
        self.assertIn("design_system_extraction.md", artifact_names)
        self.assertIn("design_system_extraction.json", artifact_names)
        self.assertIn("accessibility_report.md", artifact_names)
        self.assertIn("accessibility_report.json", artifact_names)
        self.assertIn("browser_diagnostics.md", artifact_names)
        self.assertIn("browser_diagnostics.json", artifact_names)
        self.assertIn("artifact_lineage.md", artifact_names)
        self.assertIn("artifact_lineage.json", artifact_names)
        self.assertIn("page_handoff.md", artifact_names)
        self.assertIn("page_handoff.json", artifact_names)
        self.assertIn("design_spec.md", artifact_names)
        self.assertIn("handoff_manifest.json", artifact_names)
        self.assertIn("design_tokens.json", artifact_names)
        self.assertIn("design_tokens.css", artifact_names)
        self.assertIn("design_handoff_bundle.zip", artifact_names)
        self.assertEqual(state["active_production_capability"], "design")
        self.assertEqual(state["active_production_status"], "completed")
        self.assertIn(html_paths[0], state["final_file_paths"])

        payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(payload["design_genre"], "landing_page")
        self.assertEqual(len(payload["html_artifacts"]), 1)
        self.assertEqual(payload["design_system_audit_reports"][0]["status"], "pass")
        self.assertEqual(payload["component_inventory_reports"][0]["status"], "ready")
        self.assertGreater(payload["component_inventory_reports"][0]["metrics"]["item_count"], 0)
        self.assertEqual(payload["design_system_extraction_reports"][0]["status"], "ready")
        self.assertGreater(payload["design_system_extraction_reports"][0]["metrics"]["token_count"], 0)
        self.assertGreater(payload["design_system_extraction_reports"][0]["metrics"]["selector_count"], 0)
        self.assertEqual(payload["accessibility_reports"][0]["status"], "pass")
        self.assertEqual(payload["accessibility_reports"][0]["metrics"]["h1_count"], 1)
        self.assertEqual(payload["browser_diagnostics_reports"][0]["status"], "ready")
        self.assertEqual(payload["browser_diagnostics_reports"][0]["metrics"]["preview_valid_count"], 2)
        lineage = payload["artifact_lineage_reports"][0]
        self.assertEqual(lineage["status"], "ready")
        self.assertEqual(lineage["latest_artifact_id"], payload["html_artifacts"][0]["artifact_id"])
        self.assertEqual(lineage["metrics"]["artifact_count"], 1)
        self.assertEqual(
            lineage["items"][0]["report_refs"]["accessibility_report_ids"],
            [payload["accessibility_reports"][0]["report_id"]],
        )
        self.assertEqual(
            lineage["items"][0]["report_refs"]["design_system_extraction_report_ids"],
            [payload["design_system_extraction_reports"][0]["report_id"]],
        )
        self.assertEqual(lineage["items"][0]["report_refs"]["preview_report_ids"], [item["report_id"] for item in payload["preview_reports"]])
        self.assertEqual(
            lineage["items"][0]["report_refs"]["page_handoff_report_ids"],
            [payload["page_handoff_reports"][0]["report_id"]],
        )
        page_handoff = payload["page_handoff_reports"][0]
        self.assertEqual(page_handoff["status"], "ready")
        self.assertEqual(page_handoff["metrics"]["planned_page_count"], 1)
        self.assertEqual(page_handoff["metrics"]["handoff_item_count"], 1)
        self.assertEqual(page_handoff["items"][0]["status"], "ready")
        self.assertEqual(page_handoff["items"][0]["artifact_id"], payload["html_artifacts"][0]["artifact_id"])
        rebuilt_inventory = build_component_inventory(DesignProductionState.model_validate(payload))
        self.assertEqual(rebuilt_inventory.status, "ready")
        self.assertTrue(any(item.source == "layout_plan" for item in rebuilt_inventory.items))
        design_system_view = asyncio.run(
            manager.view(
                production_session_id=result.production_session_id,
                view_type="design_system",
                adk_state=state,
            )
        )
        self.assertEqual(design_system_view.view["design_system_audit_reports"][0]["status"], "pass")
        self.assertEqual(design_system_view.view["design_system_extraction_reports"][0]["status"], "ready")
        self.assertTrue(design_system_view.view["design_system_audit_report_path"].endswith("reports/design_system_audit.md"))
        extraction_view = asyncio.run(
            manager.view(
                production_session_id=result.production_session_id,
                view_type="design_system_extraction",
                adk_state=state,
            )
        )
        self.assertEqual(extraction_view.view["latest_design_system_extraction_report"]["status"], "ready")
        self.assertTrue(extraction_view.view["design_system_extraction_report_path"].endswith("reports/design_system_extraction.md"))
        quality_view = asyncio.run(
            manager.view(
                production_session_id=result.production_session_id,
                view_type="quality",
                adk_state=state,
            )
        )
        self.assertEqual(quality_view.view["design_system_audit_reports"][0]["status"], "pass")
        self.assertEqual(quality_view.view["component_inventory_reports"][0]["status"], "ready")
        self.assertEqual(quality_view.view["design_system_extraction_reports"][0]["status"], "ready")
        self.assertEqual(quality_view.view["accessibility_reports"][0]["status"], "pass")
        components_view = asyncio.run(
            manager.view(
                production_session_id=result.production_session_id,
                view_type="components",
                adk_state=state,
            )
        )
        self.assertEqual(components_view.view["component_inventory_reports"][0]["status"], "ready")
        self.assertTrue(components_view.view["component_inventory_report_path"].endswith("reports/component_inventory.md"))
        accessibility_view = asyncio.run(
            manager.view(
                production_session_id=result.production_session_id,
                view_type="accessibility",
                adk_state=state,
            )
        )
        self.assertEqual(accessibility_view.view["latest_accessibility_report"]["status"], "pass")
        self.assertTrue(accessibility_view.view["accessibility_report_path"].endswith("reports/accessibility_report.md"))
        diagnostics_view = asyncio.run(
            manager.view(
                production_session_id=result.production_session_id,
                view_type="diagnostics",
                adk_state=state,
            )
        )
        self.assertEqual(diagnostics_view.view["latest_browser_diagnostics"]["status"], "ready")
        self.assertTrue(diagnostics_view.view["browser_diagnostics_report_path"].endswith("reports/browser_diagnostics.md"))
        lineage_view = asyncio.run(
            manager.view(
                production_session_id=result.production_session_id,
                view_type="lineage",
                adk_state=state,
            )
        )
        self.assertEqual(lineage_view.view["latest_artifact_lineage"]["status"], "ready")
        self.assertTrue(lineage_view.view["artifact_lineage_report_path"].endswith("reports/artifact_lineage.md"))
        pages_view = asyncio.run(
            manager.view(
                production_session_id=result.production_session_id,
                view_type="pages",
                adk_state=state,
            )
        )
        self.assertEqual(pages_view.view["latest_page_handoff"]["status"], "ready")
        self.assertTrue(pages_view.view["page_handoff_report_path"].endswith("reports/page_handoff.md"))
        self.assertEqual(payload["html_validation_reports"][0]["status"], "valid")
        self.assertEqual(payload["qc_reports"][0]["status"], "pass")
        export_paths = {artifact["path"] for artifact in payload["export_artifacts"]}
        self.assertEqual(len(export_paths), 5)
        self.assertTrue(any(path.endswith("exports/design_spec.md") for path in export_paths))
        token_json_path = next(path for path in export_paths if path.endswith("exports/design_tokens.json"))
        token_css_path = next(path for path in export_paths if path.endswith("exports/design_tokens.css"))
        token_json = json.loads(resolve_workspace_path(token_json_path).read_text(encoding="utf-8"))
        token_css = resolve_workspace_path(token_css_path).read_text(encoding="utf-8")
        self.assertEqual(token_json["source"], "placeholder")
        self.assertEqual(token_json["css_variables"]["--cc-color-primary"], "#165DFF")
        self.assertIn("--cc-color-primary: #165DFF;", token_css)
        manifest_path = next(path for path in export_paths if path.endswith("exports/handoff_manifest.json"))
        manifest = json.loads(resolve_workspace_path(manifest_path).read_text(encoding="utf-8"))
        self.assertEqual(manifest["latest_html_path"], html_paths[0])
        self.assertEqual(manifest["quality_status"], "pass")
        self.assertEqual(manifest["design_system_audit_reports"][0]["status"], "pass")
        self.assertEqual(manifest["component_inventory_reports"][0]["status"], "ready")
        self.assertEqual(manifest["design_system_extraction_status"], "ready")
        self.assertEqual(manifest["design_system_extraction_reports"][0]["status"], "ready")
        self.assertEqual(manifest["accessibility_status"], "pass")
        self.assertEqual(manifest["accessibility_reports"][0]["status"], "pass")
        self.assertEqual(manifest["browser_diagnostics_reports"][0]["status"], "ready")
        self.assertEqual(manifest["artifact_lineage_status"], "ready")
        self.assertEqual(manifest["artifact_lineage_reports"][0]["metrics"]["artifact_count"], 1)
        self.assertEqual(manifest["page_handoff_status"], "ready")
        self.assertEqual(manifest["page_handoff_reports"][0]["metrics"]["handoff_item_count"], 1)
        self.assertTrue(any(item["name"] == "design_tokens.json" for item in manifest["design_token_artifacts"]))
        self.assertTrue(any(item["name"] == "design_tokens.css" for item in manifest["design_token_artifacts"]))
        self.assertTrue(any(item["name"] == "design_handoff_bundle.zip" for item in manifest["handoff_artifacts"]))
        bundle_path = next(path for path in export_paths if path.endswith("exports/design_handoff_bundle.zip"))
        with zipfile.ZipFile(resolve_workspace_path(bundle_path)) as bundle:
            bundle_names = set(bundle.namelist())
        self.assertIn("artifacts/index.html", bundle_names)
        self.assertIn("exports/design_spec.md", bundle_names)
        self.assertIn("exports/handoff_manifest.json", bundle_names)
        self.assertIn("exports/design_tokens.json", bundle_names)
        self.assertIn("exports/design_tokens.css", bundle_names)
        self.assertIn("reports/design_system_audit.md", bundle_names)
        self.assertIn("reports/component_inventory.md", bundle_names)
        self.assertIn("reports/component_inventory.json", bundle_names)
        self.assertIn("reports/design_system_extraction.md", bundle_names)
        self.assertIn("reports/design_system_extraction.json", bundle_names)
        self.assertIn("reports/accessibility_report.md", bundle_names)
        self.assertIn("reports/accessibility_report.json", bundle_names)
        self.assertIn("reports/browser_diagnostics.md", bundle_names)
        self.assertIn("reports/browser_diagnostics.json", bundle_names)
        self.assertIn("reports/artifact_lineage.md", bundle_names)
        self.assertIn("reports/artifact_lineage.json", bundle_names)
        self.assertIn("reports/page_handoff.md", bundle_names)
        self.assertIn("reports/page_handoff.json", bundle_names)
        self.assertIn("reports/qc_report.md", bundle_names)
        self.assertTrue(any(name.startswith("previews/index_") and name.endswith("_desktop.png") for name in bundle_names))
        self.assertTrue(any(name.startswith("previews/index_") and name.endswith("_mobile.png") for name in bundle_names))

    def test_manager_preview_unavailable_includes_browser_remediation(self) -> None:
        state = _adk_state("session_design_preview_unavailable")
        manager = DesignProductionManager(preview_renderer=_UnavailablePreviewRenderer())

        result = asyncio.run(
            manager.start(
                user_prompt="Design a SaaS landing page for a browser diagnostics test",
                input_files=[],
                placeholder_design=True,
                design_settings=None,
                adk_state=state,
            )
        )

        payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        diagnostics = payload["browser_diagnostics_reports"][0]
        self.assertEqual(diagnostics["status"], "warning")
        self.assertEqual(diagnostics["metrics"]["preview_unavailable_count"], 2)
        self.assertEqual(diagnostics["metrics"]["browser_environment_status"], "unavailable")
        self.assertIn(
            "python -m playwright install chromium",
            diagnostics["metrics"]["browser_environment_remediation"],
        )
        environment_findings = [
            finding
            for finding in diagnostics["findings"]
            if finding["category"] == "environment"
        ]
        self.assertEqual(len(environment_findings), 2)
        self.assertTrue(
            all(
                "python -m playwright install chromium" in finding["recommendation"]
                for finding in environment_findings
            )
        )

    def test_manager_start_placeholder_multi_html_generates_all_pages(self) -> None:
        state = _adk_state("session_design_placeholder_multi")
        manager = DesignProductionManager(preview_renderer=_FakePreviewRenderer())

        result = asyncio.run(
            manager.start(
                user_prompt="Design a multi-page SaaS site for an AI support platform",
                input_files=[],
                placeholder_design=True,
                design_settings={
                    "build_mode": "multi_html",
                    "pages": [
                        {"title": "Home", "path": "index.html", "sections": ["Hero", "Feature System"]},
                        {"title": "Pricing", "path": "pricing.html", "sections": ["Plans", "FAQ"]},
                    ],
                },
                adk_state=state,
            )
        )

        self.assertEqual(result.status, "completed")
        payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(payload["build_mode"], "multi_html")
        self.assertEqual(len(payload["layout_plan"]["pages"]), 2)
        self.assertEqual(len(payload["html_artifacts"]), 2)
        self.assertEqual({Path(item["path"]).name for item in payload["html_artifacts"]}, {"index.html", "pricing.html"})
        self.assertEqual(len(payload["html_validation_reports"]), 2)
        self.assertEqual(len(payload["preview_reports"]), 4)
        self.assertEqual(len(payload["qc_reports"]), 2)
        self.assertEqual(len(payload["accessibility_reports"]), 2)
        self.assertEqual(len(payload["design_system_extraction_reports"]), 2)
        page_handoff = payload["page_handoff_reports"][0]
        self.assertEqual(page_handoff["status"], "ready")
        self.assertEqual(page_handoff["metrics"]["planned_page_count"], 2)
        self.assertEqual(page_handoff["metrics"]["ready_item_count"], 2)
        final_html_names = {Path(path).name for path in state["final_file_paths"] if path.endswith(".html")}
        self.assertEqual(final_html_names, {"index.html", "pricing.html"})
        manifest_path = next(item["path"] for item in payload["export_artifacts"] if item["path"].endswith("exports/handoff_manifest.json"))
        manifest = json.loads(resolve_workspace_path(manifest_path).read_text(encoding="utf-8"))
        manifest_deliverable_names = {item["name"] for item in manifest["deliverables"]}
        self.assertIn("index.html", manifest_deliverable_names)
        self.assertIn("pricing.html", manifest_deliverable_names)
        self.assertEqual(manifest["page_handoff_reports"][0]["metrics"]["handoff_item_count"], 2)
        bundle_path = next(item["path"] for item in payload["export_artifacts"] if item["path"].endswith("exports/design_handoff_bundle.zip"))
        with zipfile.ZipFile(resolve_workspace_path(bundle_path)) as bundle:
            bundle_names = set(bundle.namelist())
        self.assertIn("artifacts/index.html", bundle_names)
        self.assertIn("artifacts/pricing.html", bundle_names)

    def test_manager_start_real_path_returns_design_direction_review(self) -> None:
        state = _adk_state("session_design_direction_review")
        manager = DesignProductionManager(
            preview_renderer=_FakePreviewRenderer(),
            expert_runtime=_FakeDesignExpertRuntime(),
        )

        result = asyncio.run(
            manager.start(
                user_prompt="Design an operations dashboard UI for ecommerce GMV and inventory alerts",
                input_files=[],
                placeholder_design=False,
                design_settings=None,
                adk_state=state,
            )
        )

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "design_direction_review")
        self.assertEqual(result.review_payload.review_type, "design_direction_review")
        self.assertEqual(state["active_production_capability"], "design")
        self.assertEqual(state["active_production_status"], "needs_user_review")
        self.assertEqual(result.review_payload.model_dump(mode="json")["items"][0]["brief"]["design_genre"], "ui_design")

    def test_manager_real_path_approval_builds_expert_html_then_completes(self) -> None:
        state = _adk_state("session_design_expert_build")
        manager = DesignProductionManager(
            preview_renderer=_FakePreviewRenderer(),
            expert_runtime=_FakeDesignExpertRuntime(),
        )
        started = asyncio.run(
            manager.start(
                user_prompt="Design an operations dashboard UI for ecommerce GMV and inventory alerts",
                input_files=[],
                placeholder_design=False,
                design_settings=None,
                adk_state=state,
            )
        )

        preview = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        self.assertEqual(preview.status, "needs_user_review")
        self.assertEqual(preview.stage, "preview_review")
        persisted = json.loads(resolve_workspace_path(preview.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(persisted["html_artifacts"][0]["builder"], "HtmlBuilderExpert.baseline")
        html_path = persisted["html_artifacts"][0]["path"]
        self.assertIn("Expert generated HTML design", resolve_workspace_path(html_path).read_text(encoding="utf-8"))
        self.assertEqual(persisted["qc_reports"][0]["status"], "pass")
        self.assertIn("Hero reflects the requested operational dashboard brief.", persisted["qc_reports"][0]["findings"][0]["summary"])
        review_metadata = preview.review_payload.metadata
        self.assertEqual(review_metadata["delivery"]["latest_html_path"], html_path)
        self.assertEqual(review_metadata["delivery"]["preview_count"], 2)
        self.assertEqual(review_metadata["delivery"]["screenshot_count"], 2)
        self.assertEqual(review_metadata["delivery"]["html_validation_status"], "valid")
        self.assertEqual(review_metadata["delivery"]["qc_status"], "pass")
        self.assertTrue(review_metadata["delivery"]["qc_report_path"].endswith("reports/qc_report.md"))
        self.assertEqual(review_metadata["delivery"]["design_system_extraction_status"], "ready")
        self.assertTrue(review_metadata["delivery"]["design_system_extraction_report_path"].endswith("reports/design_system_extraction.md"))
        self.assertEqual(review_metadata["delivery"]["accessibility_status"], "pass")
        self.assertTrue(review_metadata["delivery"]["accessibility_report_path"].endswith("reports/accessibility_report.md"))
        self.assertEqual(review_metadata["preview"]["valid_count"], 2)
        self.assertEqual(review_metadata["preview"]["reports"][0]["layout"]["horizontal_overflow_px"], 0)
        self.assertEqual(review_metadata["design_system_extraction"]["status"], "ready")
        self.assertGreater(review_metadata["design_system_extraction"]["token_count"], 0)
        self.assertEqual(review_metadata["accessibility"]["status"], "pass")
        self.assertEqual(review_metadata["accessibility"]["finding_counts"]["warning"], 0)
        self.assertEqual(review_metadata["diagnostics"]["status"], "ready")
        self.assertEqual(review_metadata["diagnostics"]["finding_counts"]["warning"], 0)
        self.assertEqual(review_metadata["lineage"]["status"], "ready")
        self.assertEqual(review_metadata["lineage"]["artifact_count"], 1)
        self.assertTrue(review_metadata["lineage"]["report_path"].endswith("reports/artifact_lineage.md"))
        self.assertEqual(review_metadata["delivery"]["page_handoff_status"], "ready")
        self.assertTrue(review_metadata["delivery"]["page_handoff_report_path"].endswith("reports/page_handoff.md"))
        self.assertEqual(review_metadata["pages"]["status"], "ready")
        self.assertEqual(review_metadata["pages"]["handoff_item_count"], 1)
        self.assertEqual(review_metadata["quality"]["status"], "pass")
        self.assertEqual(review_metadata["quality"]["finding_counts"]["info"], 1)
        self.assertEqual(review_metadata["quality"]["attention_findings"], [])

        overview_view = asyncio.run(
            manager.view(
                production_session_id=started.production_session_id,
                view_type="overview",
                adk_state=state,
            )
        )
        self.assertEqual(overview_view.view["active_review"]["metadata"]["delivery"]["latest_html_path"], html_path)
        self.assertEqual(overview_view.view["active_review"]["metadata"]["quality"]["status"], "pass")
        self.assertEqual(overview_view.view["counts"]["design_system_extraction_reports"], 1)
        self.assertEqual(overview_view.view["counts"]["accessibility_reports"], 1)
        self.assertEqual(overview_view.view["counts"]["browser_diagnostics_reports"], 1)
        self.assertEqual(overview_view.view["counts"]["artifact_lineage_reports"], 1)
        self.assertEqual(overview_view.view["counts"]["page_handoff_reports"], 1)

        completed = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.stage, "completed")
        self.assertIn(html_path, state["final_file_paths"])
        completed_names = {artifact.name for artifact in completed.artifacts}
        self.assertIn("design_system_audit.md", completed_names)
        self.assertIn("component_inventory.md", completed_names)
        self.assertIn("component_inventory.json", completed_names)
        self.assertIn("design_system_extraction.md", completed_names)
        self.assertIn("design_system_extraction.json", completed_names)
        self.assertIn("accessibility_report.md", completed_names)
        self.assertIn("accessibility_report.json", completed_names)
        self.assertIn("design_spec.md", completed_names)
        self.assertIn("handoff_manifest.json", completed_names)
        self.assertIn("design_tokens.json", completed_names)
        self.assertIn("design_tokens.css", completed_names)
        self.assertIn("design_handoff_bundle.zip", completed_names)
        self.assertIn("artifact_lineage.md", completed_names)
        self.assertIn("artifact_lineage.json", completed_names)
        self.assertIn("page_handoff.md", completed_names)
        self.assertIn("page_handoff.json", completed_names)
        completed_payload = json.loads(resolve_workspace_path(completed.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(completed_payload["design_system_audit_reports"][0]["status"], "warning")
        self.assertEqual(completed_payload["component_inventory_reports"][0]["status"], "ready")
        self.assertEqual(completed_payload["design_system_extraction_reports"][0]["status"], "ready")
        self.assertEqual(completed_payload["accessibility_reports"][0]["status"], "pass")
        self.assertEqual(completed_payload["browser_diagnostics_reports"][0]["status"], "ready")
        self.assertEqual(completed_payload["artifact_lineage_reports"][0]["status"], "ready")
        self.assertEqual(completed_payload["page_handoff_reports"][0]["status"], "ready")
        self.assertEqual(len(completed_payload["export_artifacts"]), 5)

    def test_manager_real_path_multi_html_builds_each_planned_page(self) -> None:
        state = _adk_state("session_design_expert_multi")
        runtime = _FakeDesignExpertRuntime()
        manager = DesignProductionManager(
            preview_renderer=_FakePreviewRenderer(),
            expert_runtime=runtime,
        )
        settings = {
            "build_mode": "multi_html",
            "pages": [
                {"title": "Home", "path": "index.html"},
                {"title": "Product", "path": "product.html"},
            ],
        }
        started = asyncio.run(
            manager.start(
                user_prompt="Design a multi-page product microsite",
                input_files=[],
                placeholder_design=False,
                design_settings=settings,
                adk_state=state,
            )
        )

        preview = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        self.assertEqual(preview.status, "needs_user_review")
        self.assertEqual(preview.stage, "preview_review")
        persisted = json.loads(resolve_workspace_path(preview.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(persisted["build_mode"], "multi_html")
        self.assertEqual(len(persisted["layout_plan"]["pages"]), 2)
        self.assertEqual(len(persisted["html_artifacts"]), 2)
        self.assertEqual({Path(item["path"]).name for item in persisted["html_artifacts"]}, {"index.html", "product.html"})
        self.assertEqual(len(runtime.build_calls), 2)
        self.assertTrue(all(len(call["page_ids"]) == 1 for call in runtime.build_calls))
        self.assertEqual(persisted["page_handoff_reports"][0]["status"], "ready")
        self.assertEqual(persisted["page_handoff_reports"][0]["metrics"]["ready_item_count"], 2)

    def test_manager_multi_html_revision_rebuilds_target_page_only(self) -> None:
        state = _adk_state("session_design_expert_multi_revision")
        runtime = _FakeDesignExpertRuntime()
        manager = DesignProductionManager(
            preview_renderer=_FakePreviewRenderer(),
            expert_runtime=runtime,
        )
        settings = {
            "build_mode": "multi_html",
            "pages": [
                {"title": "Home", "path": "index.html"},
                {"title": "Product", "path": "product.html"},
            ],
        }
        started = asyncio.run(
            manager.start(
                user_prompt="Design a multi-page product microsite",
                input_files=[],
                placeholder_design=False,
                design_settings=settings,
                adk_state=state,
            )
        )
        preview = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )
        first_state = json.loads(resolve_workspace_path(preview.state_ref or "").read_text(encoding="utf-8"))
        home_page_id = first_state["layout_plan"]["pages"][0]["page_id"]
        product_page_id = first_state["layout_plan"]["pages"][1]["page_id"]
        first_artifacts_by_page = {artifact["page_id"]: artifact for artifact in first_state["html_artifacts"]}

        revised = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={
                    "decision": "revise",
                    "notes": "Make the product page hero more product-led.",
                    "targets": [{"kind": "page", "id": product_page_id}],
                },
                adk_state=state,
            )
        )

        self.assertEqual(revised.status, "needs_user_review")
        self.assertEqual(revised.stage, "preview_review")
        self.assertEqual(revised.view["affected_page_ids"], [product_page_id])
        self.assertEqual(revised.view["affected_artifact_ids"], [first_artifacts_by_page[product_page_id]["artifact_id"]])
        persisted = json.loads(resolve_workspace_path(revised.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(len(persisted["html_artifacts"]), 3)
        home_artifact = next(artifact for artifact in persisted["html_artifacts"] if artifact["page_id"] == home_page_id)
        product_artifacts = [artifact for artifact in persisted["html_artifacts"] if artifact["page_id"] == product_page_id]
        old_product, revised_product = product_artifacts
        self.assertEqual(home_artifact["status"], "valid")
        self.assertEqual(old_product["status"], "stale")
        self.assertEqual(revised_product["status"], "valid")
        self.assertEqual(revised_product["builder"], "HtmlBuilderExpert.variant")
        self.assertTrue(Path(revised_product["path"]).name.startswith("product_v"))
        self.assertEqual(len(runtime.build_calls), 3)
        self.assertEqual(runtime.build_calls[-1]["build_mode"], "revision")
        self.assertEqual(runtime.build_calls[-1]["page_ids"], [product_page_id])
        self.assertIn("Expert generated HTML design", runtime.build_calls[-1]["previous_html"])
        self.assertEqual(persisted["page_handoff_reports"][0]["status"], "ready")
        self.assertEqual(persisted["page_handoff_reports"][0]["metrics"]["ready_item_count"], 2)
        lineage_items = {
            item["artifact_id"]: item
            for item in persisted["artifact_lineage_reports"][0]["items"]
        }
        self.assertEqual(lineage_items[old_product["artifact_id"]]["replaced_by_artifact_id"], revised_product["artifact_id"])
        self.assertEqual(lineage_items[revised_product["artifact_id"]]["replaces_artifact_ids"], [old_product["artifact_id"]])
        self.assertNotIn(home_artifact["artifact_id"], lineage_items[revised_product["artifact_id"]]["replaces_artifact_ids"])

        completed = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        self.assertEqual(completed.status, "completed")
        completed_html_names = {Path(path).name for path in state["final_file_paths"] if path.endswith(".html")}
        self.assertEqual(completed_html_names, {"index.html", Path(revised_product["path"]).name})

    def test_manager_final_approval_can_export_pdf_from_approved_html(self) -> None:
        state = _adk_state("session_design_pdf_export")
        manager = DesignProductionManager(
            preview_renderer=_FakePreviewRenderer(),
            pdf_exporter=_FakePdfExporter(),
            expert_runtime=_FakeDesignExpertRuntime(),
        )
        started = asyncio.run(
            manager.start(
                user_prompt="Design an operations dashboard UI for ecommerce GMV and inventory alerts",
                input_files=[],
                placeholder_design=False,
                design_settings={"exports": ["pdf"]},
                adk_state=state,
            )
        )
        preview = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        completed = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        self.assertEqual(preview.stage, "preview_review")
        self.assertEqual(completed.status, "completed")
        artifact_paths = {artifact.path for artifact in completed.artifacts}
        pdf_path = next(path for path in artifact_paths if path.endswith("exports/design.pdf"))
        self.assertTrue(resolve_workspace_path(pdf_path).is_file())
        self.assertIn(pdf_path, state["final_file_paths"])

        completed_payload = json.loads(resolve_workspace_path(completed.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(completed_payload["requested_exports"], ["pdf"])
        self.assertEqual(completed_payload["pdf_export_reports"][0]["status"], "exported")
        self.assertEqual(completed_payload["pdf_export_reports"][0]["pdf_path"], pdf_path)
        lineage_item = completed_payload["artifact_lineage_reports"][0]["items"][0]
        self.assertEqual(lineage_item["artifact_refs"]["pdf_paths"], [pdf_path])
        self.assertEqual(
            lineage_item["report_refs"]["pdf_export_report_ids"],
            [completed_payload["pdf_export_reports"][0]["report_id"]],
        )
        self.assertEqual(len(completed_payload["export_artifacts"]), 5)
        pdf_report_path = f"{completed_payload['production_session']['root_dir']}/reports/pdf_export_report.json"
        self.assertEqual(
            json.loads(resolve_workspace_path(pdf_report_path).read_text(encoding="utf-8"))[0]["pdf_path"],
            pdf_path,
        )

        manifest_path = next(
            artifact["path"]
            for artifact in completed_payload["export_artifacts"]
            if artifact["name"] == "handoff_manifest.json"
        )
        manifest = json.loads(resolve_workspace_path(manifest_path).read_text(encoding="utf-8"))
        self.assertEqual(manifest["pdf_export_reports"][0]["pdf_path"], pdf_path)
        self.assertEqual(manifest["browser_diagnostics_reports"][0]["metrics"]["pdf_exported_count"], 1)
        self.assertEqual(manifest["artifact_lineage_reports"][0]["items"][0]["artifact_refs"]["pdf_paths"], [pdf_path])
        self.assertTrue(any(item["path"] == pdf_path for item in manifest["deliverables"]))

        bundle_path = next(
            artifact["path"]
            for artifact in completed_payload["export_artifacts"]
            if artifact["name"] == "design_handoff_bundle.zip"
        )
        with zipfile.ZipFile(resolve_workspace_path(bundle_path)) as bundle:
            self.assertIn("exports/design.pdf", set(bundle.namelist()))

    def test_manager_pdf_export_failure_is_non_blocking(self) -> None:
        state = _adk_state("session_design_pdf_unavailable")
        manager = DesignProductionManager(
            preview_renderer=_FakePreviewRenderer(),
            pdf_exporter=_UnavailablePdfExporter(),
            expert_runtime=_FakeDesignExpertRuntime(),
        )
        started = asyncio.run(
            manager.start(
                user_prompt="Design an operations dashboard UI for ecommerce GMV and inventory alerts",
                input_files=[],
                placeholder_design=False,
                design_settings=None,
                adk_state=state,
            )
        )
        preview = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        completed = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve", "exports": ["pdf"]},
                adk_state=state,
            )
        )

        self.assertEqual(preview.stage, "preview_review")
        self.assertEqual(completed.status, "completed")
        artifact_paths = {artifact.path for artifact in completed.artifacts}
        self.assertFalse(any(path.endswith("exports/design.pdf") for path in artifact_paths))
        completed_payload = json.loads(resolve_workspace_path(completed.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(completed_payload["requested_exports"], ["pdf"])
        self.assertEqual(completed_payload["pdf_export_reports"][0]["status"], "unavailable")
        self.assertIn("Playwright is not available", completed_payload["pdf_export_reports"][0]["issues"][0])
        self.assertEqual(completed_payload["browser_diagnostics_reports"][0]["status"], "warning")
        self.assertEqual(
            completed_payload["browser_diagnostics_reports"][0]["metrics"]["browser_environment_status"],
            "unavailable",
        )
        self.assertIn(
            "python -m playwright install chromium",
            completed_payload["browser_diagnostics_reports"][0]["metrics"]["browser_environment_remediation"],
        )
        diagnostic_summaries = [
            finding["summary"]
            for finding in completed_payload["browser_diagnostics_reports"][0]["findings"]
        ]
        self.assertTrue(any("PDF export is unavailable" in summary for summary in diagnostic_summaries))
        event_types = [event["event_type"] for event in completed_payload["production_events"]]
        self.assertIn("pdf_export_unavailable", event_types)
        self.assertIn("browser_diagnostics_built", event_types)

    def test_manager_source_ref_details_flow_to_preview_and_handoff(self) -> None:
        source_dir = workspace_root() / "test_inputs" / "design_source_refs"
        source_dir.mkdir(parents=True, exist_ok=True)
        source_path = source_dir / "brand.png"
        source_path.write_bytes(b"fake-brand-image")
        source_ref = workspace_relative_path(source_path)
        state = _adk_state("session_design_source_refs")
        manager = DesignProductionManager(
            preview_renderer=_FakePreviewRenderer(),
            expert_runtime=_FakeDesignExpertRuntime(),
        )

        started = asyncio.run(
            manager.start(
                user_prompt="Design a product detail page using the provided brand image",
                input_files=[{"path": source_ref, "name": "brand.png", "description": "brand logo reference"}],
                placeholder_design=False,
                design_settings={"design_genre": "product_detail_page"},
                adk_state=state,
            )
        )
        preview = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        review_items = preview.review_payload.model_dump(mode="json")["items"]
        html_review_items = next(item for item in review_items if item["kind"] == "html_artifacts")["artifacts"]
        preview_review_items = next(item for item in review_items if item["kind"] == "preview_reports")["reports"]
        asset_id = html_review_items[0]["source_refs"][0]
        review_metadata = preview.review_payload.metadata
        self.assertEqual(html_review_items[0]["source_ref_details"][0]["name"], "brand.png")
        self.assertEqual(preview_review_items[0]["source_refs"], [asset_id])
        self.assertTrue(preview_review_items[0]["source_ref_details"][0]["path"].split("/")[-1].endswith("brand.png"))
        self.assertEqual(review_metadata["source_refs"], [asset_id])
        self.assertEqual(review_metadata["source_ref_details"][0]["name"], "brand.png")
        self.assertNotIn(str(workspace_root()), json.dumps(review_metadata, ensure_ascii=False))

        preview_view = asyncio.run(
            manager.view(
                production_session_id=started.production_session_id,
                view_type="preview",
                adk_state=state,
            )
        )
        self.assertEqual(preview_view.view["html_artifacts"][0]["source_refs"], [asset_id])
        self.assertEqual(preview_view.view["preview_reports"][0]["source_ref_details"][0]["name"], "brand.png")

        completed = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )
        artifacts_view = asyncio.run(
            manager.view(
                production_session_id=started.production_session_id,
                view_type="artifacts",
                adk_state=state,
            )
        )
        self.assertEqual(artifacts_view.view["html_artifacts"][0]["source_ref_details"][0]["name"], "brand.png")
        self.assertTrue(any(item["source_refs"] == [asset_id] for item in artifacts_view.view["artifacts"]))

        completed_payload = json.loads(resolve_workspace_path(completed.state_ref or "").read_text(encoding="utf-8"))
        manifest_path = next(
            artifact["path"]
            for artifact in completed_payload["export_artifacts"]
            if artifact["name"] == "handoff_manifest.json"
        )
        spec_path = next(
            artifact["path"]
            for artifact in completed_payload["export_artifacts"]
            if artifact["name"] == "design_spec.md"
        )
        manifest = json.loads(resolve_workspace_path(manifest_path).read_text(encoding="utf-8"))
        spec_markdown = resolve_workspace_path(spec_path).read_text(encoding="utf-8")
        self.assertEqual(manifest["latest_source_refs"], [asset_id])
        self.assertEqual(manifest["latest_source_ref_details"][0]["name"], "brand.png")
        self.assertEqual(manifest["html_artifacts"][0]["source_ref_details"][0]["asset_id"], asset_id)
        self.assertEqual(manifest["preview_reports"][0]["source_refs"], [asset_id])
        self.assertTrue(any(item["source_refs"] == [asset_id] for item in manifest["deliverables"]))
        self.assertTrue(any(item["source_refs"] == [asset_id] for item in manifest["handoff_artifacts"]))
        self.assertIn(f"brand.png({asset_id})", spec_markdown)
        self.assertNotIn(str(workspace_root()), spec_markdown)

    def test_manager_expert_quality_failure_becomes_warning(self) -> None:
        state = _adk_state("session_design_expert_qc_fallback")
        runtime = _FakeDesignExpertRuntime(fail_quality=True)
        manager = DesignProductionManager(
            preview_renderer=_FakePreviewRenderer(),
            expert_runtime=runtime,
        )
        started = asyncio.run(
            manager.start(
                user_prompt="Design an operations dashboard UI for ecommerce GMV and inventory alerts",
                input_files=[],
                placeholder_design=False,
                design_settings=None,
                adk_state=state,
            )
        )

        preview = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        self.assertEqual(preview.status, "needs_user_review")
        self.assertEqual(preview.stage, "preview_review")
        persisted = json.loads(resolve_workspace_path(preview.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(len(runtime.quality_calls), 1)
        self.assertEqual(persisted["qc_reports"][0]["status"], "warning")
        summaries = [finding["summary"] for finding in persisted["qc_reports"][0]["findings"]]
        self.assertTrue(any("DesignQCExpert failed" in summary for summary in summaries))
        review_metadata = preview.review_payload.metadata
        self.assertEqual(review_metadata["quality"]["status"], "warning")
        self.assertEqual(review_metadata["quality"]["finding_counts"]["warning"], 1)
        self.assertTrue(
            any("DesignQCExpert failed" in finding["summary"] for finding in review_metadata["quality"]["attention_findings"])
        )

    def test_manager_revision_impact_marks_target_section(self) -> None:
        state = _adk_state("session_design_revision_impact")
        manager = DesignProductionManager(
            preview_renderer=_FakePreviewRenderer(),
            expert_runtime=_FakeDesignExpertRuntime(),
        )
        started = asyncio.run(
            manager.start(
                user_prompt="Design an operations dashboard UI for ecommerce GMV and inventory alerts",
                input_files=[],
                placeholder_design=False,
                design_settings=None,
                adk_state=state,
            )
        )

        result = asyncio.run(
            manager.analyze_revision_impact(
                production_session_id=started.production_session_id,
                user_response={
                    "notes": "Make the hero more product-led.",
                    "targets": [{"type": "section", "id": "hero"}],
                },
                adk_state=state,
            )
        )

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.view["view_type"], "revision_impact")
        self.assertIn("hero", result.view["affected_section_ids"])
        self.assertTrue(result.view["affected_page_ids"])
        self.assertEqual(result.view["recommended_action"], "rebuild_page")

    def test_manager_preview_review_revise_rebuilds_variant_html(self) -> None:
        state = _adk_state("session_design_revision_rebuild")
        runtime = _FakeDesignExpertRuntime()
        manager = DesignProductionManager(
            preview_renderer=_FakePreviewRenderer(),
            expert_runtime=runtime,
        )
        started = asyncio.run(
            manager.start(
                user_prompt="Design an operations dashboard UI for ecommerce GMV and inventory alerts",
                input_files=[],
                placeholder_design=False,
                design_settings=None,
                adk_state=state,
            )
        )
        preview = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )
        first_state = json.loads(resolve_workspace_path(preview.state_ref or "").read_text(encoding="utf-8"))
        first_html_path = first_state["html_artifacts"][0]["path"]

        revised = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={
                    "decision": "revise",
                    "notes": "Make the hero more product-led.",
                    "targets": [{"type": "section", "id": "hero"}],
                },
                adk_state=state,
            )
        )

        self.assertEqual(revised.status, "needs_user_review")
        self.assertEqual(revised.stage, "preview_review")
        self.assertEqual(revised.view["view_type"], "revision_impact")
        self.assertIn("hero", revised.view["affected_section_ids"])
        persisted = json.loads(resolve_workspace_path(revised.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(len(persisted["html_artifacts"]), 2)
        old_artifact, new_artifact = persisted["html_artifacts"]
        self.assertEqual(old_artifact["status"], "stale")
        self.assertIn("Make the hero", old_artifact["stale_reason"])
        self.assertEqual(new_artifact["builder"], "HtmlBuilderExpert.variant")
        self.assertEqual(new_artifact["status"], "valid")
        self.assertNotEqual(first_html_path, new_artifact["path"])
        self.assertIn("Expert revised HTML design", resolve_workspace_path(new_artifact["path"]).read_text(encoding="utf-8"))
        lineage = persisted["artifact_lineage_reports"][0]
        self.assertEqual(lineage["metrics"]["artifact_count"], 2)
        self.assertEqual(lineage["metrics"]["stale_artifact_count"], 1)
        old_lineage, new_lineage = lineage["items"]
        self.assertEqual(old_lineage["artifact_id"], old_artifact["artifact_id"])
        self.assertEqual(old_lineage["replaced_by_artifact_id"], new_artifact["artifact_id"])
        self.assertEqual(new_lineage["artifact_id"], new_artifact["artifact_id"])
        self.assertEqual(new_lineage["build_mode"], "revision")
        self.assertIn(old_artifact["artifact_id"], new_lineage["replaces_artifact_ids"])
        self.assertEqual(new_lineage["revision_id"], persisted["revision_history"][0]["revision_id"])
        self.assertEqual(runtime.build_calls[-1]["build_mode"], "revision")
        self.assertIn("Expert generated HTML design", runtime.build_calls[-1]["previous_html"])
        self.assertIn("hero", runtime.build_calls[-1]["revision_impact"]["affected_section_ids"])
        self.assertEqual(persisted["revision_history"][0]["impact"]["recommended_action"], "rebuild_page")

        completed = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        self.assertEqual(completed.status, "completed")
        self.assertIn(new_artifact["path"], state["final_file_paths"])
        self.assertNotIn(first_html_path, state["final_file_paths"])

    def test_manager_apply_revision_rebuilds_existing_html(self) -> None:
        state = _adk_state("session_design_apply_revision")
        runtime = _FakeDesignExpertRuntime()
        manager = DesignProductionManager(
            preview_renderer=_FakePreviewRenderer(),
            expert_runtime=runtime,
        )
        started = asyncio.run(
            manager.start(
                user_prompt="Design an operations dashboard UI for ecommerce GMV and inventory alerts",
                input_files=[],
                placeholder_design=False,
                design_settings=None,
                adk_state=state,
            )
        )
        asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        result = asyncio.run(
            manager.apply_revision(
                production_session_id=started.production_session_id,
                user_response="Make the hero more product-led.",
                adk_state=state,
            )
        )

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "preview_review")
        persisted = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(persisted["html_artifacts"][0]["status"], "stale")
        self.assertEqual(persisted["html_artifacts"][1]["builder"], "HtmlBuilderExpert.variant")
        self.assertEqual(runtime.build_calls[-1]["revision_request"]["notes"], "Make the hero more product-led.")

    def test_manager_view_is_read_only(self) -> None:
        state = _adk_state("session_design_view")
        manager = DesignProductionManager(
            preview_renderer=_FakePreviewRenderer(),
            expert_runtime=_FakeDesignExpertRuntime(),
        )
        started = asyncio.run(
            manager.start(
                user_prompt="Design a product detail page for a compact coffee machine",
                input_files=[],
                placeholder_design=False,
                design_settings=None,
                adk_state=state,
            )
        )
        before = json.dumps(state, sort_keys=True)

        result = asyncio.run(
            manager.view(
                production_session_id=started.production_session_id,
                view_type="layout",
                adk_state=state,
            )
        )

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.view["view_type"], "layout")
        self.assertEqual(result.view["layout_plan"]["pages"][0]["title"], "Expert Product Detail Page")
        self.assertEqual(json.dumps(state, sort_keys=True), before)

    def test_tool_requires_tool_context(self) -> None:
        result = asyncio.run(run_design_production(action="start", placeholder_design=True))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["capability"], "design")
        self.assertEqual(result["stage"], "missing_tool_context")

    def test_tool_view_defaults_to_active_session(self) -> None:
        state = _adk_state("session_design_tool_view")
        tool_context = SimpleNamespace(state=state)
        started = asyncio.run(
            run_design_production(
                action="start",
                user_prompt="Design a landing page",
                placeholder_design=True,
                tool_context=tool_context,
            )
        )
        self.assertEqual(started["status"], "completed")

        result = asyncio.run(
            run_design_production(
                action="view",
                view_type="lineage",
                tool_context=tool_context,
            )
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["view"]["view_type"], "lineage")
        self.assertIn("artifact_lineage_reports", result["view"])

    def test_manager_artifacts_view_includes_handoff_exports(self) -> None:
        state = _adk_state("session_design_artifacts_view")
        manager = DesignProductionManager(preview_renderer=_FakePreviewRenderer())
        completed = asyncio.run(
            manager.start(
                user_prompt="Design a landing page",
                input_files=[],
                placeholder_design=True,
                design_settings=None,
                adk_state=state,
            )
        )

        result = asyncio.run(
            manager.view(
                production_session_id=completed.production_session_id,
                view_type="artifacts",
                adk_state=state,
            )
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.view["view_type"], "artifacts")
        export_names = {artifact["name"] for artifact in result.view["export_artifacts"]}
        self.assertEqual(
            export_names,
            {
                "design_spec.md",
                "handoff_manifest.json",
                "design_tokens.json",
                "design_tokens.css",
                "design_handoff_bundle.zip",
            },
        )
        final_names = {artifact["name"] for artifact in result.view["artifacts"]}
        self.assertTrue(export_names.issubset(final_names))

    def test_design_system_audit_flags_invalid_tokens(self) -> None:
        report = audit_design_system(
            DesignSystemSpec(
                colors=[
                    DesignTokenColor(name="primary", value="#nothex"),
                    DesignTokenColor(name="primary", value="#111111"),
                ],
                typography=[
                    DesignTokenTypography(role="display", font_family="Inter", font_size_px=-1),
                ],
                spacing={},
                radii={"default": "16px"},
                component_tokens={},
            )
        )

        self.assertEqual(report.status, "fail")
        summaries = [finding.summary for finding in report.findings]
        self.assertTrue(any("invalid hex" in summary for summary in summaries))
        self.assertTrue(any("Duplicate color token name" in summary for summary in summaries))
        self.assertEqual(report.metrics["finding_counts"]["error"], 2)

    def test_design_system_extraction_reads_css_tokens_selectors_and_breakpoints(self) -> None:
        session_root = workspace_root() / "generated" / "session_design_extraction" / "production" / "design_extraction"
        artifacts_dir = session_root / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        html_path = artifacts_dir / "index.html"
        html_path.write_text(
            """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <style>
    :root { --primary: #123456; --gap: 12px; }
    .card { color: var(--primary); gap: var(--gap); border-radius: 8px; font-family: Inter, sans-serif; }
    #hero > .card { box-shadow: 0 12px 40px rgba(0, 0, 0, 0.18); padding: 24px; }
    @media (max-width: 720px) { .card { padding: 16px; } }
  </style>
</head>
<body><main id="hero"><section class="card"><h1>Design extraction</h1></section></main></body>
</html>
""",
            encoding="utf-8",
        )
        artifact = HtmlArtifact(
            page_id="page_extraction",
            path=workspace_relative_path(html_path),
            builder="placeholder",
        )
        state = DesignProductionState(
            production_session=ProductionSession(
                production_session_id="design_extraction",
                capability="design",
                adk_session_id="session_design_extraction",
                turn_index=1,
                root_dir=workspace_relative_path(session_root),
                status="running",
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
            ),
            status="running",
            stage="design_system_extraction_test",
            design_system=DesignSystemSpec(
                colors=[DesignTokenColor(name="primary", value="#123456")],
                typography=[DesignTokenTypography(role="body", font_family="Inter, sans-serif")],
                spacing={"gap": "12px"},
                radii={"default": "8px"},
                shadows={},
            ),
            html_artifacts=[artifact],
        )

        report = build_design_system_extraction(state, artifact=artifact)

        self.assertEqual(report.status, "ready")
        categories = {token.category for token in report.tokens}
        self.assertTrue({"css_variable", "color", "typography", "spacing", "radius", "shadow", "breakpoint"}.issubset(categories))
        variable_tokens = {token.name: token for token in report.tokens if token.category == "css_variable"}
        self.assertEqual(variable_tokens["--primary"].value, "#123456")
        self.assertGreaterEqual(variable_tokens["--primary"].usage_count, 1)
        self.assertTrue(any(selector.selector == ".card" for selector in report.selectors))
        self.assertTrue(any(selector.kind == "media_query" for selector in report.selectors))
        self.assertGreater(report.metrics["selector_count"], 0)

    def test_page_handoff_reports_missing_planned_pages(self) -> None:
        session_root = workspace_root() / "generated" / "session_design_page_handoff" / "production" / "design_page_handoff"
        html_path = session_root / "artifacts" / "index.html"
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text("<!doctype html><html lang=\"en\"><head><title>Home</title></head><body></body></html>", encoding="utf-8")
        home_page = PageBlueprint(page_id="page_home", title="Home", path="index.html")
        pricing_page = PageBlueprint(page_id="page_pricing", title="Pricing", path="pricing.html")
        artifact = HtmlArtifact(
            artifact_id="artifact_home",
            page_id=home_page.page_id,
            path=workspace_relative_path(html_path),
            builder="placeholder",
            status="valid",
        )
        state = DesignProductionState(
            production_session=ProductionSession(
                production_session_id="design_page_handoff",
                capability="design",
                adk_session_id="session_design_page_handoff",
                turn_index=1,
                root_dir=workspace_relative_path(session_root),
                status="running",
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
            ),
            status="running",
            stage="page_handoff_test",
            build_mode="multi_html",
            layout_plan=LayoutPlan(pages=[home_page, pricing_page]),
            html_artifacts=[artifact],
            html_validation_reports=[
                HtmlValidationReport(
                    artifact_id=artifact.artifact_id,
                    path=artifact.path,
                    status="valid",
                )
            ],
            preview_reports=[
                PreviewReport(
                    artifact_id=artifact.artifact_id,
                    viewport="desktop",
                    screenshot_path="generated/session_design_page_handoff/production/design_page_handoff/previews/home.png",
                    valid=True,
                )
            ],
        )

        report = build_page_handoff(state)

        self.assertEqual(report.status, "partial")
        self.assertEqual(report.metrics["planned_page_count"], 2)
        self.assertEqual(report.metrics["handoff_item_count"], 2)
        self.assertEqual(report.metrics["ready_item_count"], 1)
        self.assertEqual(report.metrics["missing_item_count"], 1)
        statuses_by_page = {item.page_id: item.status for item in report.items}
        self.assertEqual(statuses_by_page["page_home"], "ready")
        self.assertEqual(statuses_by_page["page_pricing"], "missing")

    def test_accessibility_report_flags_static_html_issues(self) -> None:
        session_root = workspace_root() / "generated" / "session_design_accessibility" / "production" / "design_accessibility"
        artifacts_dir = session_root / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        html_path = artifacts_dir / "index.html"
        html_path.write_text(
            """<!doctype html>
<html>
<head></head>
<body>
  <section><h2>Skipped heading</h2></section>
  <img src="product.png">
  <button><span aria-hidden="true"></span></button>
  <form><input id="email" type="email" placeholder="Email"></form>
  <div onclick="submitForm()">Submit</div>
</body>
</html>
""",
            encoding="utf-8",
        )
        artifact = HtmlArtifact(
            page_id="page_accessibility",
            path=workspace_relative_path(html_path),
            builder="placeholder",
        )
        state = DesignProductionState(
            production_session=ProductionSession(
                production_session_id="design_accessibility",
                capability="design",
                adk_session_id="session_design_accessibility",
                turn_index=1,
                root_dir=workspace_relative_path(session_root),
                status="running",
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
            ),
            status="running",
            stage="accessibility_test",
            html_artifacts=[artifact],
        )

        report = build_accessibility_report(state, artifact=artifact)

        self.assertEqual(report.status, "fail")
        summaries = [finding.summary for finding in report.findings]
        self.assertTrue(any("missing a lang" in summary for summary in summaries))
        self.assertTrue(any("missing an alt" in summary for summary in summaries))
        self.assertTrue(any("no accessible name" in summary for summary in summaries))
        self.assertTrue(any("no durable accessible label" in summary for summary in summaries))
        self.assertTrue(any("onclick handler" in summary for summary in summaries))
        self.assertEqual(report.metrics["image_missing_alt_count"], 1)
        self.assertEqual(report.metrics["unlabeled_form_control_count"], 1)

    def test_design_prompt_catalog_renders_packaged_templates(self) -> None:
        self.assertIn("html_builder_expert", available_prompt_templates())
        self.assertIn("design_qc_expert", available_prompt_templates())
        self.assertIn("layout_planner_expert", available_prompt_templates())

        rendered = render_prompt_template(
            "brief_expert",
            {
                "design_genre": "landing_page",
                "user_prompt": "Design a launch page",
                "design_settings_json": "{}",
                "reference_assets_json": "[]",
                "playbook_text": "Landing page playbook",
            },
        )

        self.assertIn("Design a launch page", rendered)
        qc_rendered = render_prompt_template(
            "design_qc_expert",
            {
                "brief_json": "{}",
                "design_system_json": "{}",
                "layout_plan_json": "{}",
                "artifact_json": "{}",
                "validation_report_json": "{}",
                "preview_reports_json": "[]",
                "html_summary": "<html></html>",
            },
        )
        self.assertIn("Preview reports JSON", qc_rendered)
        with self.assertRaises(DesignPromptCatalogError):
            render_prompt_template("brief_expert", {"user_prompt": "missing variables"})

    def test_design_expert_runtime_adk_output_schemas_are_strict(self) -> None:
        for schema_model in (_AdkDesignSystemSpec, _AdkLayoutPlan, _AdkHtmlBuildOutput):
            with self.subTest(schema=schema_model.__name__):
                _assert_no_open_object_schema(self, schema_model.model_json_schema())

    def test_design_expert_runtime_strict_outputs_convert_to_production_models(self) -> None:
        design_system = _AdkDesignSystemSpec(
            version=2,
            source="unexpected",
            colors=[
                _AdkDesignTokenColor(name="primary", value="#123456", usage="Primary actions"),
                _AdkDesignTokenColor(name="", value="#ffffff", usage="Ignored missing name"),
            ],
            typography=[
                _AdkDesignTokenTypography(
                    role="display",
                    font_family="Inter, sans-serif",
                    font_size_px=42,
                    font_weight="700",
                    line_height="1.1",
                )
            ],
            spacing=[_AdkNamedValue(name="section_y", value="32px")],
            radii=[_AdkNamedValue(name="card", value="8px")],
            shadows=[_AdkNamedValue(name="focus", value="0 0 0 3px rgba(22, 93, 255, 0.24)")],
            component_tokens=[
                _AdkComponentToken(
                    name="button",
                    tokens=[
                        _AdkNamedValue(name="height", value="44px"),
                        _AdkNamedValue(name="", value="ignored"),
                    ],
                )
            ],
            notes="Strict schema conversion.",
        ).to_design_system_spec()
        self.assertEqual(design_system.source, "generated")
        self.assertEqual(design_system.version, 2)
        self.assertEqual([color.name for color in design_system.colors], ["primary"])
        self.assertEqual(design_system.spacing, {"section_y": "32px"})
        self.assertEqual(design_system.radii, {"card": "8px"})
        self.assertEqual(design_system.component_tokens, {"button": {"height": "44px"}})

        layout_plan = _AdkLayoutPlan(
            pages=[
                _AdkPageBlueprint(
                    page_id="page_home",
                    title="Home",
                    path="index.html",
                    status="unexpected",
                    sections=[
                        _AdkLayoutSection(
                            section_id="hero",
                            title="Hero",
                            purpose="Introduce the product.",
                            content=["Lead with product value."],
                            expert_hints=[_AdkNamedValue(name="density", value="compact")],
                        )
                    ],
                )
            ],
            global_notes="Keep page rhythm stable.",
        ).to_layout_plan()
        self.assertEqual(layout_plan.pages[0].status, "draft")
        self.assertEqual(layout_plan.pages[0].sections[0].section_id, "hero")
        self.assertEqual(layout_plan.pages[0].sections[0].expert_hints, {"density": "compact"})

        html_output = _AdkHtmlBuildOutput(
            title="Home",
            html="<!doctype html><html lang=\"en\"><body><main></main></body></html>",
            section_fragments=[
                _AdkSectionFragment(section_id="hero", html="<section id=\"hero\"></section>"),
                _AdkSectionFragment(section_id="", html="<section></section>"),
            ],
            notes="Built from strict schema.",
        ).to_html_build_output()
        self.assertEqual(html_output.section_fragments, {"hero": "<section id=\"hero\"></section>"})

    def test_design_expert_runtime_layout_prompt_includes_multi_page_settings(self) -> None:
        runtime = _CapturingDesignExpertRuntime()
        settings = {
            "build_mode": "multi_html",
            "pages": [
                {"title": "Home", "path": "index.html", "purpose": "Introduce the product"},
                {"title": "Product", "path": "product.html", "purpose": "Show details"},
            ],
        }

        plan = asyncio.run(
            runtime.plan_direction(
                user_prompt="Design a multi-page product microsite",
                design_genre="landing_page",
                design_settings=settings,
                reference_assets=[],
            )
        )

        layout_request = next(
            item["request_text"]
            for item in runtime.requests
            if item["agent_name"] == "LayoutPlannerExpert"
        )
        self.assertEqual(len(plan.layout_plan.pages), 2)
        self.assertIn("Requested build mode:\nmulti_html", layout_request)
        self.assertIn('"path": "product.html"', layout_request)
        self.assertIn("produce one `PageBlueprint` per requested page spec", layout_request)

    def test_html_validator_rejects_local_absolute_paths(self) -> None:
        session_root = workspace_root() / "generated" / "session_design_validator" / "production" / "design_validator"
        artifacts_dir = session_root / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        html_path = artifacts_dir / "index.html"
        html_path.write_text(
            """<!doctype html><html><body><img src="/Users/example/private.png" id="hero"></body></html>""",
            encoding="utf-8",
        )

        report = HtmlValidator().validate(
            workspace_relative_path(html_path),
            session_root=session_root,
            artifact_id="html_artifact_test",
        )

        self.assertEqual(report.status, "invalid")
        self.assertTrue(any("absolute path" in issue for issue in report.issues))


def _assert_no_open_object_schema(test_case: unittest.TestCase, node: Any, *, path: str = "$") -> None:
    """Assert every object-like JSON schema node forbids additional properties."""
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            test_case.assertIs(
                node.get("additionalProperties"),
                False,
                f"{path} must set additionalProperties=false for strict ADK structured output",
            )
        for key, value in node.items():
            _assert_no_open_object_schema(test_case, value, path=f"{path}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _assert_no_open_object_schema(test_case, item, path=f"{path}[{index}]")


if __name__ == "__main__":
    unittest.main()
