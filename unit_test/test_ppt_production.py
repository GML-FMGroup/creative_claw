import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.production.ppt.ingest import classify_input_path, ingest_input_files
from src.production.ppt.manager import PPTProductionManager
from src.production.ppt.models import SlidePreview
from src.production.ppt.prompt_catalog import PPTPromptCatalogError, available_prompt_templates, render_prompt_template
from src.production.ppt.tool import run_ppt_production
from src.runtime.workspace import workspace_relative_path, workspace_root


class _FakePreviewRenderer:
    def render(self, *, pptx_path, deck_spec, render_settings, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        previews = []
        for slide in deck_spec.slides:
            path = output_dir / f"slide-{slide.sequence_index:02d}.png"
            path.write_bytes(b"fake-preview")
            previews.append(
                SlidePreview(
                    slide_id=slide.slide_id,
                    sequence_index=slide.sequence_index,
                    preview_path=workspace_relative_path(path),
                    metadata={"renderer": "fake"},
                )
            )
        return previews


def _adk_state(sid="session_ppt_test"):
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


class PPTProductionTests(unittest.TestCase):
    def test_classify_input_path_roles(self) -> None:
        self.assertEqual(classify_input_path("template.pptx"), "template_pptx")
        self.assertEqual(classify_input_path("report.pdf"), "source_doc")
        self.assertEqual(classify_input_path("logo.png"), "reference_image")
        self.assertEqual(classify_input_path("archive.zip"), "unknown")

    def test_ingest_input_files_records_supported_and_p0_warnings(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            root = Path(tmpdir)
            template = root / "template.pptx"
            report = root / "report.pdf"
            image = root / "logo.png"
            unknown = root / "data.zip"
            for path in (template, report, image, unknown):
                path.write_bytes(b"fake")

            entries = ingest_input_files(
                [
                    workspace_relative_path(template),
                    workspace_relative_path(report),
                    workspace_relative_path(image),
                    workspace_relative_path(unknown),
                ],
                turn_index=3,
            )

        self.assertEqual([entry.role for entry in entries], ["template_pptx", "source_doc", "reference_image", "unknown"])
        self.assertEqual(entries[0].status, "valid")
        self.assertIn("P0", entries[1].warning)
        self.assertIn("P0", entries[2].warning)
        self.assertEqual(entries[3].status, "unsupported")

    def test_prompt_catalog_renders_and_rejects_missing_variables(self) -> None:
        self.assertIn("outline_instruction", available_prompt_templates())
        rendered = render_prompt_template(
            "outline_instruction",
            {"brief": "Build a quarterly review", "target_pages": 5, "style_preset": "business_executive"},
        )
        self.assertIn("Build a quarterly review", rendered)
        with self.assertRaises(PPTPromptCatalogError):
            render_prompt_template("outline_instruction", {"brief": "Missing fields"})

    def test_manager_native_flow_outline_preview_completion(self) -> None:
        state = _adk_state("session_ppt_manager_flow")
        manager = PPTProductionManager(preview_renderer=_FakePreviewRenderer())

        started = asyncio.run(
            manager.start(
                user_prompt="做一份 3 页的 Q1 业务汇报，给高管看。",
                input_files=[],
                placeholder_assets=False,
                render_settings={"target_pages": 3, "style_preset": "business_executive"},
                adk_state=state,
            )
        )

        self.assertEqual(started.status, "needs_user_review")
        self.assertEqual(started.stage, "outline_review")
        self.assertEqual(started.review_payload.review_type, "ppt_outline_review")
        self.assertEqual(state["active_production_capability"], "ppt")

        preview_review = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        self.assertEqual(preview_review.status, "needs_user_review")
        self.assertEqual(preview_review.stage, "final_preview_review")
        self.assertTrue(any(artifact.name == "final.pptx" for artifact in preview_review.artifacts))
        final_artifact = next(artifact for artifact in preview_review.artifacts if artifact.name == "final.pptx")
        self.assertTrue((workspace_root() / final_artifact.path).is_file())

        completed = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.stage, "completed")
        self.assertTrue(state["final_file_paths"])
        self.assertTrue(any(path.endswith("final.pptx") for path in state["final_file_paths"]))

    def test_tool_wrapper_requires_tool_context(self) -> None:
        result = asyncio.run(run_ppt_production(action="start", user_prompt="Build slides"))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "missing_tool_context")

    def test_tool_wrapper_start_uses_adk_state(self) -> None:
        tool_context = SimpleNamespace(state=_adk_state("session_ppt_tool_wrapper"))

        result = asyncio.run(
            run_ppt_production(
                action="start",
                user_prompt="Build a concise product update deck",
                render_settings={"target_pages": 4},
                tool_context=tool_context,
            )
        )

        self.assertEqual(result["capability"], "ppt")
        self.assertEqual(result["status"], "needs_user_review")
        self.assertEqual(result["stage"], "outline_review")
        self.assertEqual(tool_context.state["active_production_capability"], "ppt")


if __name__ == "__main__":
    unittest.main()
