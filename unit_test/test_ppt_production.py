import asyncio
import json
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from types import SimpleNamespace

from src.production.ppt.deck_builder import DeckBuilderService
from src.production.ppt.document_loader import DocumentLoaderService
from src.production.ppt.ingest import classify_input_path, ingest_input_files
from src.production.ppt.manager import PPTProductionManager
from src.production.ppt.models import DeckSlide, DeckSpec, PPTRenderSettings, SlidePreview
from src.production.ppt.prompt_catalog import PPTPromptCatalogError, available_prompt_templates, render_prompt_template
from src.production.ppt.template_analyzer import TemplateAnalyzerService
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


def _load_state_payload(result) -> dict:
    state_ref = result.state_ref
    if not state_ref:
        raise AssertionError("Production result did not include state_ref")
    return json.loads((workspace_root() / state_ref).read_text(encoding="utf-8"))


def _start_page_preview_review(manager: PPTProductionManager, state: dict) -> tuple[str, str, str]:
    started = asyncio.run(
        manager.start(
            user_prompt="做一份 3 页的产品策略更新。",
            input_files=[],
            placeholder_assets=False,
            render_settings={"target_pages": 3},
            adk_state=state,
        )
    )
    deck_spec_review = asyncio.run(
        manager.resume(
            production_session_id=started.production_session_id,
            user_response={"decision": "approve"},
            adk_state=state,
        )
    )
    target_slide_id = deck_spec_review.review_payload.items[1]["id"]
    untouched_slide_id = deck_spec_review.review_payload.items[0]["id"]
    asyncio.run(
        manager.resume(
            production_session_id=started.production_session_id,
            user_response={"decision": "approve"},
            adk_state=state,
        )
    )
    asyncio.run(
        manager.apply_revision(
            production_session_id=started.production_session_id,
            user_response={
                "notes": "Make the risk narrative more concrete.",
                "target_kind": "deck_slide",
                "target_id": target_slide_id,
            },
            adk_state=state,
        )
    )
    regenerated = asyncio.run(
        manager.regenerate_stale_segments(
            production_session_id=started.production_session_id,
            adk_state=state,
        )
    )
    if regenerated.stage != "page_preview_review":
        raise AssertionError("Expected setup to pause at page_preview_review")
    return started.production_session_id, target_slide_id, untouched_slide_id


def _write_minimal_docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>"
        for paragraph in paragraphs
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("word/document.xml", document_xml)


def _write_minimal_pdf(path: Path, lines: list[str]) -> None:
    escaped_lines = [
        line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        for line in lines
    ]
    text_commands = "\n".join(f"({line}) Tj" for line in escaped_lines)
    stream = f"BT\n/F1 12 Tf\n72 720 Td\n{text_commands}\nET".encode("latin-1")
    compressed = zlib.compress(stream)
    path.write_bytes(
        b"%PDF-1.4\n"
        b"1 0 obj\n"
        + f"<< /Length {len(compressed)} /Filter /FlateDecode >>\n".encode("ascii")
        + b"stream\n"
        + compressed
        + b"\nendstream\nendobj\n%%EOF\n"
    )


def _build_template_pptx(output_path: Path) -> str:
    deck_spec = DeckSpec(
        title="Template Deck",
        slides=[
            DeckSlide(
                slide_id="template_slide_1",
                sequence_index=1,
                title="Template Cover",
                layout_type="cover",
                bullets=["Template signal"],
            ),
            DeckSlide(
                slide_id="template_slide_2",
                sequence_index=2,
                title="Template Content",
                layout_type="content",
                bullets=["Reusable content treatment"],
            ),
        ],
    )
    return DeckBuilderService().build(
        deck_spec=deck_spec,
        render_settings=PPTRenderSettings(aspect_ratio="16:9", style_preset="business_executive"),
        output_path=output_path,
    )


class PPTProductionTests(unittest.TestCase):
    def test_classify_input_path_roles(self) -> None:
        self.assertEqual(classify_input_path("template.pptx"), "template_pptx")
        self.assertEqual(classify_input_path("report.pdf"), "source_doc")
        self.assertEqual(classify_input_path("logo.png"), "reference_image")
        self.assertEqual(classify_input_path("archive.zip"), "unknown")

    def test_ingest_input_files_records_supported_inputs_and_warnings(self) -> None:
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
        self.assertEqual(entries[1].warning, "")
        self.assertIn("Reference images", entries[2].warning)
        self.assertEqual(entries[3].status, "unsupported")

    def test_document_loader_extracts_txt_and_docx_sources(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            root = Path(tmpdir)
            txt = root / "source.txt"
            docx = root / "notes.docx"
            txt.write_text("Executive Summary\nRevenue grew 20% in Q1. Retention improved across enterprise accounts.", encoding="utf-8")
            _write_minimal_docx(docx, ["Customer expansion pipeline reached 3.2M ARR.", "Sales cycle risk remains concentrated in finance approvals."])
            entries = ingest_input_files([workspace_relative_path(txt), workspace_relative_path(docx)], turn_index=1)
            summary = DocumentLoaderService().build_summary(entries)

        self.assertEqual(summary.status, "ready")
        self.assertEqual(summary.document_count, 2)
        self.assertGreater(summary.extracted_character_count, 0)
        self.assertTrue(any("Revenue grew 20%" in fact for fact in summary.salient_facts))
        self.assertTrue(any("Customer expansion" in fact for fact in summary.salient_facts))

    def test_document_loader_extracts_simple_pdf_text_layer(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            root = Path(tmpdir)
            pdf = root / "source.pdf"
            _write_minimal_pdf(pdf, ["Revenue grew 20% in Q1.", "Retention improved across enterprise accounts."])
            entries = ingest_input_files([workspace_relative_path(pdf)], turn_index=1)
            summary = DocumentLoaderService().build_summary(entries)

        self.assertEqual(summary.status, "ready")
        self.assertEqual(summary.document_count, 1)
        self.assertGreater(summary.extracted_character_count, 0)
        self.assertTrue(any("Revenue grew 20%" in fact for fact in summary.salient_facts))
        self.assertEqual(summary.warnings, [])

    def test_document_loader_degrades_pdf_without_text_layer_explicitly(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            root = Path(tmpdir)
            pdf = root / "scanned.pdf"
            pdf.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Length 12 >>\nstream\nq 0 0 1 1\nendstream\nendobj\n%%EOF")
            entries = ingest_input_files([workspace_relative_path(pdf)], turn_index=1)
            summary = DocumentLoaderService().build_summary(entries)

        self.assertEqual(summary.status, "unsupported")
        self.assertTrue(any("No extractable PDF text layer" in warning for warning in summary.warnings))

    def test_template_analyzer_extracts_pptx_structure(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            root = Path(tmpdir)
            template_path = root / "template.pptx"
            template_ref = _build_template_pptx(template_path)
            entries = ingest_input_files([template_ref], turn_index=1)
            summary = TemplateAnalyzerService().build_summary(entries)

        self.assertEqual(summary.status, "ready")
        self.assertEqual(summary.slide_count, 2)
        self.assertGreaterEqual(summary.layout_count, 1)
        self.assertIn("native PPTX generation remains active", summary.summary)

    def test_prompt_catalog_renders_and_rejects_missing_variables(self) -> None:
        self.assertIn("outline_instruction", available_prompt_templates())
        rendered = render_prompt_template(
            "outline_instruction",
            {"brief": "Build a quarterly review", "target_pages": 5, "style_preset": "business_executive"},
        )
        self.assertIn("Build a quarterly review", rendered)
        with self.assertRaises(PPTPromptCatalogError):
            render_prompt_template("outline_instruction", {"brief": "Missing fields"})

    def test_manager_uses_document_and_template_summaries(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            root = Path(tmpdir)
            source = root / "business.md"
            source.write_text("Revenue grew 20% in Q1. Enterprise retention improved by 6 points.", encoding="utf-8")
            template_ref = _build_template_pptx(root / "template.pptx")

            state = _adk_state("session_ppt_input_context")
            manager = PPTProductionManager(preview_renderer=_FakePreviewRenderer())
            started = asyncio.run(
                manager.start(
                    user_prompt="做一份 3 页的 Q1 业务汇报，给高管看。",
                    input_files=[workspace_relative_path(source), template_ref],
                    placeholder_assets=False,
                    render_settings={"target_pages": 3, "style_preset": "business_executive"},
                    adk_state=state,
                )
            )

            document_view = asyncio.run(
                manager.view(
                    production_session_id=started.production_session_id,
                    view_type="document_summary",
                    adk_state=state,
                )
            )
            template_view = asyncio.run(
                manager.view(
                    production_session_id=started.production_session_id,
                    view_type="template_summary",
                    adk_state=state,
                )
            )

        self.assertEqual(started.status, "needs_user_review")
        self.assertEqual(document_view.view["document_summary"]["status"], "ready")
        self.assertEqual(template_view.view["template_summary"]["status"], "ready")
        outline_items = started.review_payload.items
        outline_bullets = [bullet for item in outline_items for bullet in item["bullet_points"]]
        self.assertTrue(any("Source fact: Revenue grew 20%" in bullet for bullet in outline_bullets))
        self.assertTrue(any("Template analyzed" in bullet for bullet in outline_bullets))

    def test_manager_native_flow_deck_spec_preview_completion(self) -> None:
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

        deck_spec_review = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        self.assertEqual(deck_spec_review.status, "needs_user_review")
        self.assertEqual(deck_spec_review.stage, "deck_spec_review")
        self.assertEqual(deck_spec_review.review_payload.review_type, "ppt_deck_spec_review")
        self.assertFalse(any(artifact.name == "final.pptx" for artifact in deck_spec_review.artifacts))

        deck_spec_view = asyncio.run(
            manager.view(
                production_session_id=started.production_session_id,
                view_type="deck_spec",
                adk_state=state,
            )
        )
        self.assertEqual(deck_spec_view.view["deck_spec"]["status"], "draft")
        self.assertEqual(len(deck_spec_view.view["deck_spec"]["slides"]), 3)

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
        manifest_artifact = next(artifact for artifact in preview_review.artifacts if artifact.name == "render_manifest.md")
        self.assertTrue((workspace_root() / manifest_artifact.path).is_file())
        segment_paths = [item["segment_path"] for item in preview_review.review_payload.items]
        self.assertEqual(len(segment_paths), 3)
        self.assertTrue(all(path.endswith(".pptx") for path in segment_paths))
        for segment_path in segment_paths:
            resolved_segment = workspace_root() / segment_path
            self.assertTrue(resolved_segment.is_file())
            with zipfile.ZipFile(resolved_segment) as package:
                slide_xml = [name for name in package.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
            self.assertEqual(len(slide_xml), 1)

        manifest_view = asyncio.run(
            manager.view(
                production_session_id=started.production_session_id,
                view_type="manifest",
                adk_state=state,
            )
        )
        manifest = manifest_view.view["manifest"]
        self.assertEqual(manifest["delivery"]["final_pptx_path"], final_artifact.path)
        self.assertEqual(manifest["delivery"]["preview_count"], 3)
        self.assertEqual(manifest["delivery"]["segment_count"], 3)
        self.assertEqual(manifest["delivery"]["quality_status"], "pass")
        self.assertEqual(len(manifest["slides"]), 3)
        self.assertEqual([slide["segment_path"] for slide in manifest["slides"]], segment_paths)
        manifest_json = workspace_root() / manifest_view.view["manifest_path"]
        self.assertTrue(manifest_json.is_file())
        self.assertEqual(json.loads(manifest_json.read_text(encoding="utf-8"))["delivery"]["final_pptx_path"], final_artifact.path)

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
        self.assertFalse(any("/segments/" in path for path in state["final_file_paths"]))

    def test_manager_can_pause_at_brief_review_before_outline(self) -> None:
        state = _adk_state("session_ppt_brief_review")
        manager = PPTProductionManager(preview_renderer=_FakePreviewRenderer())

        started = asyncio.run(
            manager.start(
                user_prompt="做一份 3 页的产品策略更新，给管理层看。",
                input_files=[],
                placeholder_assets=False,
                render_settings={"target_pages": 3, "style_preset": "pitch_deck", "brief_review": True},
                adk_state=state,
            )
        )
        payload = _load_state_payload(started)

        self.assertEqual(started.status, "needs_user_review")
        self.assertEqual(started.stage, "brief_review")
        self.assertEqual(started.review_payload.review_type, "ppt_brief_review")
        self.assertIsNone(payload["outline"])
        self.assertTrue(payload["render_settings"]["brief_review"])
        self.assertEqual(started.review_payload.items[0]["target_pages"], 3)
        self.assertEqual(started.review_payload.items[0]["style_preset"], "pitch_deck")

        approved = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )
        approved_payload = _load_state_payload(approved)

        self.assertEqual(approved.status, "needs_user_review")
        self.assertEqual(approved.stage, "outline_review")
        self.assertEqual(approved.review_payload.review_type, "ppt_outline_review")
        self.assertEqual(len(approved.review_payload.items), 3)
        self.assertIsNotNone(approved_payload["outline"])

    def test_brief_review_revise_stays_at_brief_review(self) -> None:
        state = _adk_state("session_ppt_brief_review_revise")
        manager = PPTProductionManager(preview_renderer=_FakePreviewRenderer())

        started = asyncio.run(
            manager.start(
                user_prompt="做一份 4 页的产品策略更新。",
                input_files=[],
                placeholder_assets=False,
                render_settings={"target_pages": 4, "brief_review": True},
                adk_state=state,
            )
        )

        revised = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "revise", "notes": "Audience is CFO and finance leadership."},
                adk_state=state,
            )
        )
        payload = _load_state_payload(revised)

        self.assertEqual(revised.status, "needs_user_review")
        self.assertEqual(revised.stage, "brief_review")
        self.assertEqual(revised.review_payload.review_type, "ppt_brief_review")
        self.assertIsNone(payload["outline"])
        self.assertIn("Audience is CFO and finance leadership.", payload["brief_summary"])
        self.assertEqual(revised.review_payload.items[0]["brief_summary"], payload["brief_summary"])
        self.assertEqual(payload["revision_history"][0]["stage"], "brief_review")

    def test_revision_impact_prefers_deck_slide_for_slide_number(self) -> None:
        state = _adk_state("session_ppt_revision_impact_slide_number")
        manager = PPTProductionManager(preview_renderer=_FakePreviewRenderer())

        started = asyncio.run(
            manager.start(
                user_prompt="做一份 3 页的产品策略更新。",
                input_files=[],
                placeholder_assets=False,
                render_settings={"target_pages": 3},
                adk_state=state,
            )
        )
        deck_spec_review = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        impact = asyncio.run(
            manager.analyze_revision_impact(
                production_session_id=started.production_session_id,
                user_response={"notes": "强化第二页的风险说明。", "slide_number": 2},
                adk_state=state,
            )
        )

        expected_slide_id = deck_spec_review.review_payload.items[1]["id"]
        self.assertEqual(impact.view["matched_targets"][0]["kind"], "deck_slide")
        self.assertEqual(impact.view["matched_targets"][0]["id"], expected_slide_id)
        self.assertIn(f"deck_slide:{expected_slide_id}", impact.view["stale_items"])
        self.assertIn(f"slide_preview:{expected_slide_id}", impact.view["stale_items"])
        self.assertEqual(impact.view["state_mutation"], "none")

    def test_apply_revision_updates_targeted_deck_slide_only(self) -> None:
        state = _adk_state("session_ppt_revision_deck_slide")
        manager = PPTProductionManager(preview_renderer=_FakePreviewRenderer())

        started = asyncio.run(
            manager.start(
                user_prompt="做一份 3 页的产品策略更新。",
                input_files=[],
                placeholder_assets=False,
                render_settings={"target_pages": 3},
                adk_state=state,
            )
        )
        deck_spec_review = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )
        target_slide_id = deck_spec_review.review_payload.items[1]["id"]
        untouched_slide_id = deck_spec_review.review_payload.items[0]["id"]
        preview_review = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )
        self.assertTrue(preview_review.artifacts)

        applied = asyncio.run(
            manager.apply_revision(
                production_session_id=started.production_session_id,
                user_response={
                    "notes": "Make the risk narrative more concrete.",
                    "target_kind": "deck_slide",
                    "target_id": target_slide_id,
                },
                adk_state=state,
            )
        )
        payload = _load_state_payload(applied)
        slides = {slide["slide_id"]: slide for slide in payload["deck_spec"]["slides"]}

        self.assertEqual(applied.status, "needs_user_review")
        self.assertEqual(applied.stage, "deck_spec_review")
        self.assertEqual(payload["deck_spec"]["status"], "draft")
        self.assertIn("Revision note: Make the risk narrative more concrete.", slides[target_slide_id]["bullets"])
        self.assertNotIn("Revision note: Make the risk narrative more concrete.", slides[untouched_slide_id]["bullets"])
        previews = {preview["slide_id"]: preview for preview in payload["slide_previews"]}
        self.assertEqual(previews[target_slide_id]["status"], "stale")
        self.assertEqual(previews[untouched_slide_id]["status"], "generated")
        self.assertTrue(previews[target_slide_id]["segment_path"].endswith(".pptx"))
        self.assertTrue((workspace_root() / previews[target_slide_id]["segment_path"]).is_file())
        self.assertIsNone(payload["final_artifact"])
        self.assertFalse(any(artifact["name"] == "final.pptx" for artifact in payload["artifacts"]))
        self.assertTrue(any("Stale preview" in artifact["description"] for artifact in payload["artifacts"]))
        self.assertIn(f"deck_slide:{target_slide_id}", payload["stale_items"])
        self.assertIn(f"slide_preview:{target_slide_id}", payload["stale_items"])
        self.assertIn("final", payload["stale_items"])
        self.assertIn("quality", payload["stale_items"])
        review_items = {item["id"]: item for item in applied.review_payload.items}
        self.assertEqual(review_items[target_slide_id]["preview_status"], "stale")
        self.assertEqual(review_items[untouched_slide_id]["preview_status"], "generated")

        overview = asyncio.run(
            manager.view(
                production_session_id=started.production_session_id,
                view_type="overview",
                adk_state=state,
            )
        )
        self.assertEqual(overview.view["counts"]["stale_previews"], 1)
        self.assertIn(f"slide_preview:{target_slide_id}", overview.view["stale_items"])

    def test_regenerate_stale_segments_refreshes_target_preview_only(self) -> None:
        state = _adk_state("session_ppt_regenerate_stale_segments")
        manager = PPTProductionManager(preview_renderer=_FakePreviewRenderer())

        started = asyncio.run(
            manager.start(
                user_prompt="做一份 3 页的产品策略更新。",
                input_files=[],
                placeholder_assets=False,
                render_settings={"target_pages": 3},
                adk_state=state,
            )
        )
        deck_spec_review = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )
        target_slide_id = deck_spec_review.review_payload.items[1]["id"]
        untouched_slide_id = deck_spec_review.review_payload.items[0]["id"]
        preview_review = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )
        original_items = {item["slide_id"]: item for item in preview_review.review_payload.items}
        original_untouched_preview = original_items[untouched_slide_id]["preview_path"]
        original_untouched_segment = original_items[untouched_slide_id]["segment_path"]

        applied = asyncio.run(
            manager.apply_revision(
                production_session_id=started.production_session_id,
                user_response={
                    "notes": "Make the risk narrative more concrete.",
                    "target_kind": "deck_slide",
                    "target_id": target_slide_id,
                },
                adk_state=state,
            )
        )
        self.assertEqual(applied.review_payload.items[1]["preview_status"], "stale")

        regenerated = asyncio.run(
            manager.regenerate_stale_segments(
                production_session_id=started.production_session_id,
                adk_state=state,
            )
        )
        payload = _load_state_payload(regenerated)
        previews = {preview["slide_id"]: preview for preview in payload["slide_previews"]}

        self.assertEqual(regenerated.status, "needs_user_review")
        self.assertEqual(regenerated.stage, "page_preview_review")
        self.assertEqual(regenerated.review_payload.review_type, "ppt_page_preview_review")
        self.assertEqual([item["slide_id"] for item in regenerated.review_payload.items], [target_slide_id])
        self.assertEqual(previews[target_slide_id]["status"], "generated")
        self.assertEqual(previews[untouched_slide_id]["status"], "generated")
        self.assertTrue((workspace_root() / previews[target_slide_id]["preview_path"]).is_file())
        self.assertTrue((workspace_root() / previews[target_slide_id]["segment_path"]).is_file())
        self.assertEqual(previews[untouched_slide_id]["preview_path"], original_untouched_preview)
        self.assertEqual(previews[untouched_slide_id]["segment_path"], original_untouched_segment)
        self.assertIsNone(payload["final_artifact"])
        self.assertIsNone(payload["quality_report"])
        self.assertNotIn(f"slide_preview:{target_slide_id}", payload["stale_items"])
        self.assertIn(f"deck_slide:{target_slide_id}", payload["stale_items"])
        self.assertIn("final", payload["stale_items"])
        self.assertIn("quality", payload["stale_items"])
        self.assertTrue(regenerated.review_payload.items[0]["segment_path"].endswith(".pptx"))
        self.assertFalse(any(artifact["name"] == "final.pptx" for artifact in payload["artifacts"]))

        overview = asyncio.run(
            manager.view(
                production_session_id=started.production_session_id,
                view_type="overview",
                adk_state=state,
            )
        )
        self.assertEqual(overview.view["counts"]["stale_previews"], 0)

        page_approved = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )
        approved_payload = _load_state_payload(page_approved)
        approved_previews = {preview["slide_id"]: preview for preview in approved_payload["slide_previews"]}
        approved_review_items = {item["id"]: item for item in page_approved.review_payload.items}

        self.assertEqual(page_approved.stage, "deck_spec_review")
        self.assertEqual(page_approved.review_payload.review_type, "ppt_deck_spec_review")
        self.assertEqual(approved_previews[target_slide_id]["status"], "approved")
        self.assertNotIn(f"deck_slide:{target_slide_id}", approved_payload["stale_items"])
        self.assertIn("final", approved_payload["stale_items"])
        self.assertIn("quality", approved_payload["stale_items"])
        self.assertEqual(approved_review_items[target_slide_id]["preview_status"], "approved")
        self.assertTrue(any("Approved preview" in artifact["description"] for artifact in approved_payload["artifacts"]))

        rebuilt = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        self.assertEqual(rebuilt.stage, "final_preview_review")
        self.assertTrue(any(artifact.name == "final.pptx" for artifact in rebuilt.artifacts))
        self.assertEqual(_load_state_payload(rebuilt)["stale_items"], [])

    def test_page_preview_revise_defaults_to_reviewed_slide(self) -> None:
        state = _adk_state("session_ppt_page_preview_revise_scope")
        manager = PPTProductionManager(preview_renderer=_FakePreviewRenderer())
        production_session_id, target_slide_id, untouched_slide_id = _start_page_preview_review(manager, state)

        revised = asyncio.run(
            manager.resume(
                production_session_id=production_session_id,
                user_response={"decision": "revise", "notes": "Tighten the page headline."},
                adk_state=state,
            )
        )
        payload = _load_state_payload(revised)
        slides = {slide["slide_id"]: slide for slide in payload["deck_spec"]["slides"]}
        previews = {preview["slide_id"]: preview for preview in payload["slide_previews"]}

        self.assertEqual(revised.stage, "deck_spec_review")
        self.assertIn("Revision note: Tighten the page headline.", slides[target_slide_id]["bullets"])
        self.assertNotIn("Revision note: Tighten the page headline.", slides[untouched_slide_id]["bullets"])
        self.assertEqual(previews[target_slide_id]["status"], "stale")
        self.assertEqual(previews[untouched_slide_id]["status"], "generated")
        self.assertIn(f"deck_slide:{target_slide_id}", payload["stale_items"])
        self.assertIn(f"slide_preview:{target_slide_id}", payload["stale_items"])
        self.assertIn("final", payload["stale_items"])
        self.assertIn("quality", payload["stale_items"])
        self.assertIsNone(payload["final_artifact"])
        self.assertIsNone(payload["quality_report"])
        self.assertEqual(revised.review_payload.review_type, "ppt_deck_spec_review")

    def test_page_preview_analyze_defaults_to_reviewed_slide(self) -> None:
        state = _adk_state("session_ppt_page_preview_analyze_scope")
        manager = PPTProductionManager(preview_renderer=_FakePreviewRenderer())
        production_session_id, target_slide_id, _ = _start_page_preview_review(manager, state)

        impact = asyncio.run(
            manager.analyze_revision_impact(
                production_session_id=production_session_id,
                user_response={"notes": "Tighten the page headline."},
                adk_state=state,
            )
        )
        payload = _load_state_payload(impact)

        self.assertEqual(impact.stage, "page_preview_review")
        self.assertEqual(payload["stage"], "page_preview_review")
        self.assertEqual(impact.view["state_mutation"], "none")
        self.assertEqual(impact.view["matched_targets"][0]["kind"], "deck_slide")
        self.assertEqual(impact.view["matched_targets"][0]["id"], target_slide_id)
        self.assertIn(f"deck_slide:{target_slide_id}", impact.view["stale_items"])
        self.assertIn(f"slide_preview:{target_slide_id}", impact.view["stale_items"])
        self.assertIn("final", impact.view["stale_items"])
        self.assertIn("quality", impact.view["stale_items"])

    def test_page_preview_apply_defaults_to_reviewed_slide(self) -> None:
        state = _adk_state("session_ppt_page_preview_apply_scope")
        manager = PPTProductionManager(preview_renderer=_FakePreviewRenderer())
        production_session_id, target_slide_id, untouched_slide_id = _start_page_preview_review(manager, state)

        applied = asyncio.run(
            manager.apply_revision(
                production_session_id=production_session_id,
                user_response={"notes": "Tighten the page headline."},
                adk_state=state,
            )
        )
        payload = _load_state_payload(applied)
        slides = {slide["slide_id"]: slide for slide in payload["deck_spec"]["slides"]}
        previews = {preview["slide_id"]: preview for preview in payload["slide_previews"]}

        self.assertEqual(applied.stage, "deck_spec_review")
        self.assertIn("Revision note: Tighten the page headline.", slides[target_slide_id]["bullets"])
        self.assertNotIn("Revision note: Tighten the page headline.", slides[untouched_slide_id]["bullets"])
        self.assertEqual(previews[target_slide_id]["status"], "stale")
        self.assertEqual(previews[untouched_slide_id]["status"], "generated")
        self.assertIn(f"deck_slide:{target_slide_id}", payload["stale_items"])
        self.assertIn(f"slide_preview:{target_slide_id}", payload["stale_items"])
        self.assertIn("final", payload["stale_items"])
        self.assertIn("quality", payload["stale_items"])
        self.assertIsNone(payload["final_artifact"])
        self.assertIsNone(payload["quality_report"])

    def test_apply_revision_updates_targeted_outline_entry(self) -> None:
        state = _adk_state("session_ppt_revision_outline_entry")
        manager = PPTProductionManager(preview_renderer=_FakePreviewRenderer())

        started = asyncio.run(
            manager.start(
                user_prompt="做一份 3 页的客户成功复盘。",
                input_files=[],
                placeholder_assets=False,
                render_settings={"target_pages": 3},
                adk_state=state,
            )
        )
        target_entry_id = started.review_payload.items[0]["id"]
        untouched_entry_id = started.review_payload.items[1]["id"]

        applied = asyncio.run(
            manager.apply_revision(
                production_session_id=started.production_session_id,
                user_response={
                    "notes": "Add a clearer customer retention metric.",
                    "target_kind": "outline_entry",
                    "target_id": target_entry_id,
                },
                adk_state=state,
            )
        )
        payload = _load_state_payload(applied)
        entries = {entry["slide_id"]: entry for entry in payload["outline"]["entries"]}

        self.assertEqual(applied.status, "needs_user_review")
        self.assertEqual(applied.stage, "outline_review")
        self.assertIn("Revision note: Add a clearer customer retention metric.", entries[target_entry_id]["bullet_points"])
        self.assertNotIn("Revision note: Add a clearer customer retention metric.", entries[untouched_entry_id]["bullet_points"])
        self.assertIsNone(payload["deck_spec"])
        self.assertEqual(payload["slide_previews"], [])
        self.assertIn(f"outline_entry:{target_entry_id}", payload["stale_items"])
        self.assertIn("deck_spec", payload["stale_items"])
        self.assertIn("slide_previews", payload["stale_items"])

    def test_manager_can_skip_deck_spec_review(self) -> None:
        state = _adk_state("session_ppt_skip_deck_spec_review")
        manager = PPTProductionManager(preview_renderer=_FakePreviewRenderer())

        started = asyncio.run(
            manager.start(
                user_prompt="做一份 2 页的项目更新。",
                input_files=[],
                placeholder_assets=False,
                render_settings={"target_pages": 2, "deck_spec_review": False},
                adk_state=state,
            )
        )

        preview_review = asyncio.run(
            manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )

        self.assertEqual(preview_review.status, "needs_user_review")
        self.assertEqual(preview_review.stage, "final_preview_review")
        self.assertEqual(preview_review.review_payload.review_type, "ppt_final_preview_review")

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
