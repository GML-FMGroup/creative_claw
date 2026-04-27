import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.production.design.expert_runtime import DesignDirectionPlan, HtmlBuildOutput
from src.production.design.manager import DesignProductionManager
from src.production.design.models import (
    DesignBrief,
    DesignSystemSpec,
    DesignTokenColor,
    DesignTokenTypography,
    LayoutPlan,
    LayoutSection,
    PageBlueprint,
    PreviewReport,
)
from src.production.design.prompt_catalog import (
    DesignPromptCatalogError,
    available_prompt_templates,
    render_prompt_template,
)
from src.production.design.tool import run_design_production
from src.production.design.tools.html_validator import HtmlValidator
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
        desktop_path = output_dir / "index_desktop.png"
        mobile_path = output_dir / "index_mobile.png"
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


class _FakeDesignExpertRuntime:
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
        layout_plan = LayoutPlan(
            pages=[
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
            ],
            global_notes="Fake runtime layout.",
        )
        return DesignDirectionPlan(
            brief=brief,
            design_system=design_system,
            layout_plan=layout_plan,
            notes="fake direction",
        )

    async def build_html(self, *, brief, design_system, layout_plan, reference_assets):
        return HtmlBuildOutput(
            title="Expert Dashboard Design",
            html="""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Expert Dashboard Design</title>
  <style>
    body { margin: 0; font-family: Inter, system-ui, sans-serif; color: #111827; background: #f8fafc; }
    main { width: min(1040px, calc(100% - 32px)); margin: 0 auto; padding: 32px 0; }
    section { background: #fff; border: 1px solid #dbe3ef; border-radius: 8px; padding: 24px; margin: 16px 0; }
    h1 { font-size: 44px; line-height: 1.08; margin: 0; }
    @media (max-width: 720px) { h1 { font-size: 34px; } section { padding: 18px; } }
  </style>
</head>
<body>
  <main>
    <section id="hero"><h1>Expert generated HTML design</h1><p>Monitor GMV, inventory, and alerts in one place.</p></section>
    <section id="workflow"><h2>Workflow</h2><p>Prioritize exceptions and route next actions.</p></section>
  </main>
</body>
</html>
""",
            section_fragments={"hero": "Hero", "workflow": "Workflow"},
            notes="fake html",
        )


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
        self.assertEqual(state["active_production_capability"], "design")
        self.assertEqual(state["active_production_status"], "completed")
        self.assertIn(html_paths[0], state["final_file_paths"])

        payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(payload["design_genre"], "landing_page")
        self.assertEqual(len(payload["html_artifacts"]), 1)
        self.assertEqual(payload["html_validation_reports"][0]["status"], "valid")
        self.assertEqual(payload["qc_reports"][0]["status"], "pass")

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
                view_type="overview",
                tool_context=tool_context,
            )
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["view"]["view_type"], "overview")
        self.assertEqual(result["view"]["design_genre"], "landing_page")

    def test_design_prompt_catalog_renders_packaged_templates(self) -> None:
        self.assertIn("html_builder_expert", available_prompt_templates())

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
        with self.assertRaises(DesignPromptCatalogError):
            render_prompt_template("brief_expert", {"user_prompt": "missing variables"})

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


if __name__ == "__main__":
    unittest.main()
