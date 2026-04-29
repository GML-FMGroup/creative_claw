import asyncio
import json
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.production.models import ProductionSession, utc_now_iso
from src.production.ppt.deck_builder import DeckBuilderService
from src.production.ppt.document_loader import DocumentLoaderService
from src.production.ppt.ingest import classify_input_path, ingest_input_files
from src.production.ppt.manager import PPTProductionManager, _build_deck_spec, _build_outline
from src.production.ppt.models import (
    DeckSlide,
    DeckSpec,
    DocumentSummary,
    FinalArtifact,
    PPTRenderSettings,
    PPTOutline,
    PPTOutlineEntry,
    PPTProductionState,
    SlidePreview,
)
from src.production.ppt.preview_renderer import PreviewRendererService, _wrap_text
from src.production.ppt.prompt_catalog import PPTPromptCatalogError, available_prompt_templates, render_prompt_template
from src.production.ppt.quality import build_quality_report, quality_report_markdown
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


class _FallbackPreviewRenderer:
    def render(self, *, pptx_path, deck_spec, render_settings, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        previews = []
        for slide in deck_spec.slides:
            path = output_dir / f"slide-{slide.sequence_index:02d}.png"
            path.write_bytes(b"fallback-preview")
            previews.append(
                SlidePreview(
                    slide_id=slide.slide_id,
                    sequence_index=slide.sequence_index,
                    preview_path=workspace_relative_path(path),
                    metadata={
                        "renderer": "pillow_fallback",
                        "fallback_reason": "soffice_failed:test",
                    },
                )
            )
        return previews


class _FakePPTExpertRuntime:
    model_name = "fake-ppt-expert"

    def __init__(self, *, fail_outline: bool = False, fail_deck_spec: bool = False) -> None:
        self.fail_outline = fail_outline
        self.fail_deck_spec = fail_deck_spec
        self.outline_calls = []
        self.deck_spec_calls = []

    async def plan_outline(self, *, brief, settings, inputs, document_summary=None, template_summary=None):
        self.outline_calls.append(
            {
                "brief": brief,
                "target_pages": settings.target_pages,
                "input_count": len(inputs),
                "document_status": document_summary.status if document_summary is not None else "",
                "template_status": template_summary.status if template_summary is not None else "",
            }
        )
        if self.fail_outline:
            raise RuntimeError("outline runtime unavailable")
        return _build_outline(
            brief=brief,
            settings=settings,
            inputs=inputs,
            document_summary=document_summary,
            template_summary=template_summary,
        )

    async def build_deck_spec(self, *, outline, settings, inputs, document_summary=None, template_summary=None):
        self.deck_spec_calls.append(
            {
                "outline_id": outline.outline_id,
                "slide_count": len(outline.entries),
                "input_count": len(inputs),
                "document_status": document_summary.status if document_summary is not None else "",
                "template_status": template_summary.status if template_summary is not None else "",
            }
        )
        if self.fail_deck_spec:
            raise RuntimeError("deck-spec runtime unavailable")
        return _build_deck_spec(outline, settings)


def _ppt_manager(*, preview_renderer=None, expert_runtime=None) -> PPTProductionManager:
    return PPTProductionManager(
        preview_renderer=preview_renderer or _FakePreviewRenderer(),
        expert_runtime=expert_runtime or _FakePPTExpertRuntime(),
    )


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
        self.assertIn("lightweight visual context", entries[2].warning)
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
            {
                "brief": "Build a quarterly review",
                "target_pages": 5,
                "style_preset": "business_executive",
                "input_context": "{}",
            },
        )
        self.assertIn("Build a quarterly review", rendered)
        with self.assertRaises(PPTPromptCatalogError):
            render_prompt_template("outline_instruction", {"brief": "Missing fields"})

    def test_preview_renderer_fallback_records_reason(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            root = Path(tmpdir)
            deck_spec = DeckSpec(
                title="Fallback Deck",
                slides=[
                    DeckSlide(
                        slide_id="slide_1",
                        sequence_index=1,
                        title="Fallback Slide",
                        bullets=["Fallback preview content."],
                    )
                ],
            )

            with patch("src.production.ppt.preview_renderer.shutil.which", return_value=None):
                previews = PreviewRendererService().render(
                    pptx_path="missing.pptx",
                    deck_spec=deck_spec,
                    render_settings=PPTRenderSettings(),
                    output_dir=root / "preview",
                )
            preview_file_exists = (workspace_root() / previews[0].preview_path).is_file()

        self.assertEqual(len(previews), 1)
        self.assertEqual(previews[0].metadata["renderer"], "pillow_fallback")
        self.assertEqual(previews[0].metadata["fallback_reason"], "soffice_not_found")
        self.assertTrue(preview_file_exists)

    def test_preview_renderer_sorts_pdftoppm_outputs_by_page_number(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            root = Path(tmpdir)
            pptx_path = root / "deck.pptx"
            pptx_path.write_bytes(b"fake pptx")
            deck_spec = DeckSpec(
                title="Ten Page Deck",
                slides=[
                    DeckSlide(
                        slide_id=f"slide_{index}",
                        sequence_index=index,
                        title=f"Slide {index}",
                        bullets=[f"Content {index}"],
                    )
                    for index in range(1, 11)
                ],
            )

            def fake_run(command, **_kwargs):
                if Path(command[0]).name == "soffice":
                    pdf_dir = Path(command[command.index("--outdir") + 1])
                    pdf_dir.mkdir(parents=True, exist_ok=True)
                    (pdf_dir / "deck.pdf").write_bytes(b"fake pdf")
                    return SimpleNamespace(returncode=0)
                if Path(command[0]).name == "pdftoppm":
                    prefix = Path(command[-1])
                    prefix.parent.mkdir(parents=True, exist_ok=True)
                    for index in range(1, 11):
                        (prefix.parent / f"{prefix.name}-{index}.png").write_bytes(f"page-{index}".encode("ascii"))
                    return SimpleNamespace(returncode=0)
                raise AssertionError(f"Unexpected command: {command}")

            with (
                patch("src.production.ppt.preview_renderer.shutil.which", side_effect=lambda name: f"/usr/bin/{name}"),
                patch("src.production.ppt.preview_renderer.subprocess.run", side_effect=fake_run),
            ):
                previews = PreviewRendererService().render(
                    pptx_path=workspace_relative_path(pptx_path),
                    deck_spec=deck_spec,
                    render_settings=PPTRenderSettings(),
                    output_dir=root / "preview",
                )
            page_2_bytes = (workspace_root() / previews[1].preview_path).read_bytes()
            page_10_bytes = (workspace_root() / previews[9].preview_path).read_bytes()

        self.assertEqual([preview.sequence_index for preview in previews], list(range(1, 11)))
        self.assertEqual([Path(preview.preview_path).name for preview in previews], [f"slide-{index:02d}.png" for index in range(1, 11)])
        self.assertEqual(page_2_bytes, b"page-2")
        self.assertEqual(page_10_bytes, b"page-10")

    def test_preview_text_wrap_splits_long_unspaced_text(self) -> None:
        lines = _wrap_text("季度业务增长显著留存继续改善", max_chars=6)

        self.assertGreater(len(lines), 1)
        self.assertTrue(all(len(line) <= 6 for line in lines))

    def test_build_deck_spec_shapes_metric_slide_visible_bullets(self) -> None:
        outline = PPTOutline(
            title="Metric Deck",
            target_pages=1,
            entries=[
                PPTOutlineEntry(
                    sequence_index=1,
                    title="Current Signal",
                    purpose="Summarize the signal.",
                    layout_type="metric",
                    bullet_points=["Revenue +20%", "Retention +6 pts", "Pipeline 4M", "Finance approval risk"],
                    speaker_notes="Explain the metrics.",
                )
            ],
        )

        deck_spec = _build_deck_spec(outline, PPTRenderSettings())
        slide = deck_spec.slides[0]

        self.assertEqual(slide.bullets, ["Revenue +20%", "Retention +6 pts", "Pipeline 4M"])
        self.assertIn("Additional metric context", slide.speaker_notes)
        self.assertIn("Finance approval risk", slide.speaker_notes)

    def test_manager_uses_document_and_template_summaries(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            root = Path(tmpdir)
            source = root / "business.md"
            source.write_text("Revenue grew 20% in Q1. Enterprise retention improved by 6 points.", encoding="utf-8")
            template_ref = _build_template_pptx(root / "template.pptx")

            state = _adk_state("session_ppt_input_context")
            manager = _ppt_manager()
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

    def test_manager_routes_outline_and_deck_spec_through_ppt_expert_runtime(self) -> None:
        state = _adk_state("session_ppt_expert_runtime")
        expert_runtime = _FakePPTExpertRuntime()
        manager = _ppt_manager(expert_runtime=expert_runtime)

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
        payload = _load_state_payload(deck_spec_review)
        event_types = [event["event_type"] for event in payload["production_events"]]

        self.assertEqual(len(expert_runtime.outline_calls), 1)
        self.assertEqual(expert_runtime.outline_calls[0]["target_pages"], 3)
        self.assertEqual(len(expert_runtime.deck_spec_calls), 1)
        self.assertEqual(expert_runtime.deck_spec_calls[0]["slide_count"], 3)
        self.assertIn("ppt_outline_expert_planned", event_types)
        self.assertIn("ppt_deck_spec_expert_planned", event_types)

    def test_manager_falls_back_when_ppt_outline_expert_fails(self) -> None:
        state = _adk_state("session_ppt_expert_runtime_fallback")
        expert_runtime = _FakePPTExpertRuntime(fail_outline=True)
        manager = _ppt_manager(expert_runtime=expert_runtime)

        started = asyncio.run(
            manager.start(
                user_prompt="做一份 3 页的产品策略更新。",
                input_files=[],
                placeholder_assets=False,
                render_settings={"target_pages": 3},
                adk_state=state,
            )
        )
        payload = _load_state_payload(started)
        event_types = [event["event_type"] for event in payload["production_events"]]

        self.assertEqual(started.stage, "outline_review")
        self.assertEqual(len(expert_runtime.outline_calls), 1)
        self.assertTrue(any("PPT outline expert failed" in warning for warning in payload["warnings"]))
        self.assertIn("ppt_outline_expert_failed", event_types)
        self.assertEqual(len(payload["outline"]["entries"]), 3)

    def test_reference_images_enter_lightweight_visual_context(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            root = Path(tmpdir)
            image = root / "brand_mood.png"
            image.write_bytes(b"fake image")
            image_ref = workspace_relative_path(image)

            state = _adk_state("session_ppt_reference_image_context")
            manager = _ppt_manager()
            started = asyncio.run(
                manager.start(
                    user_prompt="做一份 3 页的产品发布 PPT，参考这张品牌氛围图。",
                    input_files=[{"path": image_ref, "description": "dark premium product mood reference"}],
                    placeholder_assets=False,
                    render_settings={"target_pages": 3},
                    adk_state=state,
                )
            )

        payload = _load_state_payload(started)
        cover_bullets = payload["outline"]["entries"][0]["bullet_points"]

        self.assertEqual(started.stage, "outline_review")
        self.assertEqual(payload["inputs"][0]["role"], "reference_image")
        self.assertIn("lightweight visual context", payload["inputs"][0]["warning"])
        self.assertTrue(any("Reference image context attached for visual direction" in bullet for bullet in cover_bullets))
        self.assertTrue(any("brand_mood.png" in bullet for bullet in cover_bullets))

    def test_quality_report_tracks_source_fact_coverage(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            root = Path(tmpdir)
            source = root / "business.md"
            source.write_text("Revenue grew 20% in Q1. Enterprise retention improved by 6 points.", encoding="utf-8")
            source_ref = workspace_relative_path(source)

            state = _adk_state("session_ppt_source_fact_coverage")
            manager = _ppt_manager()
            started = asyncio.run(
                manager.start(
                    user_prompt="做一份 3 页的 Q1 业务汇报，给高管看。",
                    input_files=[source_ref],
                    placeholder_assets=False,
                    render_settings={"target_pages": 3, "style_preset": "business_executive"},
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
            preview_review = asyncio.run(
                manager.resume(
                    production_session_id=started.production_session_id,
                    user_response={"decision": "approve"},
                    adk_state=state,
                )
            )
            quality_view = asyncio.run(
                manager.view(
                    production_session_id=started.production_session_id,
                    view_type="quality",
                    adk_state=state,
                )
            )
            previews_view = asyncio.run(
                manager.view(
                    production_session_id=started.production_session_id,
                    view_type="previews",
                    adk_state=state,
                )
            )
            manifest_view = asyncio.run(
                manager.view(
                    production_session_id=started.production_session_id,
                    view_type="manifest",
                    adk_state=state,
                )
            )

        payload = _load_state_payload(preview_review)
        checks = {check["check_id"]: check for check in payload["quality_report"]["checks"]}
        quality_checks = {check["check_id"]: check for check in quality_view.view["quality_report"]["checks"]}
        source_input_ids = payload["document_summary"]["source_input_ids"]
        deck_slides_with_refs = [slide for slide in payload["deck_spec"]["slides"] if slide["source_refs"]]
        review_items_with_refs = [item for item in deck_spec_review.review_payload.items if item["source_refs"]]
        preview_items_with_refs = [item for item in preview_review.review_payload.items if item["source_refs"]]
        preview_view_items_with_refs = [item for item in previews_view.view["previews"] if item["source_refs"]]
        manifest_slides_with_refs = [slide for slide in manifest_view.view["manifest"]["slides"] if slide["source_refs"]]
        deck_spec_md = workspace_root() / f"{payload['production_session']['root_dir']}/deck_spec.md"

        self.assertEqual(preview_review.stage, "final_preview_review")
        self.assertEqual(checks["source_fact_coverage"]["status"], "pass")
        self.assertGreaterEqual(checks["source_fact_coverage"]["details"]["matched_fact_count"], 1)
        self.assertEqual(checks["source_fact_coverage"]["details"]["source_document_details"][0]["name"], "business.md")
        self.assertEqual(checks["source_fact_coverage"]["details"]["source_document_details"][0]["path"], source_ref)
        self.assertTrue(checks["source_fact_coverage"]["details"]["matched_facts"])
        self.assertEqual(quality_checks["source_fact_coverage"]["status"], "pass")
        self.assertEqual(quality_checks["source_fact_coverage"]["details"]["source_document_details"][0]["name"], "business.md")
        self.assertEqual(deck_slides_with_refs[0]["source_refs"], source_input_ids)
        self.assertEqual(review_items_with_refs[0]["source_refs"], source_input_ids)
        self.assertEqual(preview_items_with_refs[0]["source_refs"], source_input_ids)
        self.assertEqual(preview_view_items_with_refs[0]["source_refs"], source_input_ids)
        self.assertIn("title", preview_items_with_refs[0])
        self.assertIn("deck_slide_status", preview_view_items_with_refs[0])
        self.assertEqual(review_items_with_refs[0]["source_ref_details"][0]["name"], "business.md")
        self.assertEqual(preview_items_with_refs[0]["source_ref_details"][0]["name"], "business.md")
        self.assertEqual(preview_view_items_with_refs[0]["source_ref_details"][0]["path"], source_ref)
        self.assertEqual(manifest_slides_with_refs[0]["source_refs"], source_input_ids)
        self.assertEqual(manifest_slides_with_refs[0]["source_ref_details"][0]["name"], "business.md")
        self.assertIn(f"Source refs: business.md({source_input_ids[0]})", deck_spec_md.read_text(encoding="utf-8"))

    def test_quality_report_warns_when_source_facts_are_omitted(self) -> None:
        now = utc_now_iso()
        state = PPTProductionState(
            production_session=ProductionSession(
                production_session_id="ppt_source_coverage_warning",
                capability="ppt",
                adk_session_id="session_source_coverage_warning",
                turn_index=1,
                root_dir="generated/session_source_coverage_warning/production/ppt_source_coverage_warning",
                status="completed",
                created_at=now,
                updated_at=now,
            ),
            status="completed",
            stage="quality_check",
            document_summary=DocumentSummary(
                status="ready",
                salient_facts=["Revenue grew 20% in Q1. Enterprise retention improved by 6 points."],
                document_count=1,
                extracted_character_count=64,
            ),
            deck_spec=DeckSpec(
                slides=[
                    DeckSlide(
                        slide_id="slide_1",
                        sequence_index=1,
                        title="Decision and Next Steps",
                        bullets=["Confirm the owner and next checkpoint."],
                    )
                ]
            ),
        )

        report = build_quality_report(state)
        checks = {check.check_id: check for check in report.checks}

        self.assertEqual(checks["source_fact_coverage"].status, "warning")
        self.assertEqual(checks["source_fact_coverage"].details["matched_fact_count"], 0)
        self.assertEqual(checks["source_fact_coverage"].details["coverage_ratio"], 0)

        markdown = quality_report_markdown(report)
        self.assertIn("### [WARNING] source_fact_coverage", markdown)
        self.assertIn("- matched_facts: []", markdown)
        self.assertIn("- Category: content", markdown)
        self.assertIn("- matched_fact_count: 0", markdown)
        self.assertIn("- coverage_ratio: 0.0", markdown)
        self.assertIn("- unmatched_facts:", markdown)
        self.assertIn("Revenue grew 20% in Q1", markdown)

    def test_quality_report_preserves_pptx_slide_count_inspection_errors(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            root = Path(tmpdir)
            corrupt_pptx = root / "corrupt.pptx"
            corrupt_pptx.write_bytes(b"not a pptx package")
            now = utc_now_iso()
            state = PPTProductionState(
                production_session=ProductionSession(
                    production_session_id="ppt_corrupt_quality",
                    capability="ppt",
                    adk_session_id="session_corrupt_quality",
                    turn_index=1,
                    root_dir=workspace_relative_path(root),
                    status="completed",
                    created_at=now,
                    updated_at=now,
                ),
                status="completed",
                stage="quality_check",
                final_artifact=FinalArtifact(pptx_path=workspace_relative_path(corrupt_pptx)),
                deck_spec=DeckSpec(
                    slides=[
                        DeckSlide(
                            slide_id="slide_1",
                            sequence_index=1,
                            title="Executive Summary",
                            bullets=["Confirm the decision."],
                        )
                    ]
                ),
            )

            report = build_quality_report(state)

        checks = {check.check_id: check for check in report.checks}
        slide_count = checks["slide_count"]

        self.assertEqual(slide_count.status, "warning")
        self.assertEqual(slide_count.details["inspection_method"], "failed")
        self.assertEqual(slide_count.details["actual"], 0)
        self.assertIn("python_pptx_failed", slide_count.details["inspection_error"])
        self.assertIn("zip_failed", slide_count.details["inspection_error"])

    def test_quality_report_markdown_handles_missing_report(self) -> None:
        markdown = quality_report_markdown(None)

        self.assertIn("No quality report has been generated yet.", markdown)

    def test_manager_native_flow_deck_spec_preview_completion(self) -> None:
        state = _adk_state("session_ppt_manager_flow")
        manager = _ppt_manager()

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
        review_metadata = preview_review.review_payload.metadata
        self.assertEqual(review_metadata["delivery"]["final_pptx_path"], final_artifact.path)
        self.assertEqual(review_metadata["delivery"]["preview_count"], 3)
        self.assertEqual(review_metadata["delivery"]["segment_count"], 3)
        self.assertEqual(review_metadata["delivery"]["quality_status"], "pass")
        self.assertTrue(review_metadata["delivery"]["quality_report_path"].endswith("quality_report.json"))
        self.assertEqual(review_metadata["preview"]["renderers"], {"fake": 3})
        self.assertEqual(review_metadata["preview"]["fallback_count"], 0)
        self.assertEqual(review_metadata["preview"]["fallback_reasons"], [])
        self.assertEqual(review_metadata["quality"]["status"], "pass")
        self.assertEqual(review_metadata["quality"]["check_counts"]["not_applicable"], 1)
        self.assertEqual(review_metadata["quality"]["attention_checks"], [])

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

        quality_view = asyncio.run(
            manager.view(
                production_session_id=started.production_session_id,
                view_type="quality",
                adk_state=state,
            )
        )
        quality_checks = {check["check_id"]: check for check in quality_view.view["quality_report"]["checks"]}
        self.assertEqual(quality_checks["source_fact_coverage"]["status"], "not_applicable")
        overview_view = asyncio.run(
            manager.view(
                production_session_id=started.production_session_id,
                view_type="overview",
                adk_state=state,
            )
        )
        self.assertEqual(overview_view.view["active_review"]["metadata"]["quality"]["status"], "pass")

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

    def test_manager_records_preview_fallback_warning(self) -> None:
        state = _adk_state("session_ppt_preview_fallback_warning")
        manager = _ppt_manager(preview_renderer=_FallbackPreviewRenderer())

        started = asyncio.run(
            manager.start(
                user_prompt="Build a concise product update deck",
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
        preview_review = asyncio.run(
            manager.resume(
                production_session_id=deck_spec_review.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
        )
        payload = _load_state_payload(preview_review)
        review_metadata = preview_review.review_payload.metadata
        event_types = [event["event_type"] for event in payload["production_events"]]

        self.assertEqual(preview_review.stage, "final_preview_review")
        self.assertEqual(review_metadata["preview"]["renderers"], {"pillow_fallback": 3})
        self.assertEqual(review_metadata["preview"]["fallback_count"], 3)
        self.assertEqual(review_metadata["preview"]["fallback_reasons"], ["soffice_failed:test"])
        self.assertTrue(any("PPT preview rendering used Pillow fallback for 3 slide(s)" in warning for warning in payload["warnings"]))
        self.assertIn("ppt_preview_renderer_fallback", event_types)

    def test_p1_acceptance_source_document_flow_completes_with_quality_context(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            root = Path(tmpdir)
            source = root / "acceptance_business.md"
            source.write_text(
                "Revenue grew 20% in Q1. Enterprise retention improved by 6 points. "
                "Expansion pipeline reached 4M for the next quarter.",
                encoding="utf-8",
            )
            source_ref = workspace_relative_path(source)

            state = _adk_state("session_ppt_p1_acceptance_source_doc")
            manager = _ppt_manager()

            started = asyncio.run(
                manager.start(
                    user_prompt="做一份 3 页的 Q1 业务汇报，给高管看，突出收入、留存和 pipeline。",
                    input_files=[source_ref],
                    placeholder_assets=False,
                    render_settings={"target_pages": 3, "style_preset": "business_executive"},
                    adk_state=state,
                )
            )
            started_payload = _load_state_payload(started)
            source_input_ids = started_payload["document_summary"]["source_input_ids"]

            self.assertEqual(started.stage, "outline_review")
            self.assertEqual(started_payload["document_summary"]["status"], "ready")
            self.assertEqual(started_payload["document_summary"]["document_count"], 1)
            self.assertTrue(source_input_ids)
            self.assertTrue(
                any(
                    "Source fact:" in bullet
                    for item in started.review_payload.items
                    for bullet in item["bullet_points"]
                )
            )

            deck_spec_review = asyncio.run(
                manager.resume(
                    production_session_id=started.production_session_id,
                    user_response={"decision": "approve"},
                    adk_state=state,
                )
            )
            deck_items_with_refs = [item for item in deck_spec_review.review_payload.items if item["source_refs"]]

            self.assertEqual(deck_spec_review.stage, "deck_spec_review")
            self.assertTrue(deck_items_with_refs)
            self.assertEqual(deck_items_with_refs[0]["source_refs"], source_input_ids)
            self.assertEqual(deck_items_with_refs[0]["source_ref_details"][0]["name"], "acceptance_business.md")

            preview_review = asyncio.run(
                manager.resume(
                    production_session_id=started.production_session_id,
                    user_response={"decision": "approve"},
                    adk_state=state,
                )
            )
            preview_payload = _load_state_payload(preview_review)
            quality_checks = {check["check_id"]: check for check in preview_payload["quality_report"]["checks"]}
            metadata = preview_review.review_payload.metadata
            preview_items_with_refs = [item for item in preview_review.review_payload.items if item["source_refs"]]
            segment_paths = [item["segment_path"] for item in preview_review.review_payload.items]

            self.assertEqual(preview_review.stage, "final_preview_review")
            self.assertEqual(metadata["delivery"]["quality_status"], "pass")
            self.assertEqual(metadata["quality"]["status"], "pass")
            self.assertEqual(metadata["quality"]["check_counts"]["warning"], 0)
            self.assertEqual(metadata["quality"]["check_counts"]["fail"], 0)
            self.assertEqual(metadata["quality"]["attention_checks"], [])
            self.assertTrue(metadata["delivery"]["final_pptx_path"].endswith("final.pptx"))
            self.assertEqual(len(segment_paths), 3)
            self.assertTrue(all((workspace_root() / path).is_file() for path in segment_paths))
            self.assertTrue(preview_items_with_refs)
            self.assertEqual(preview_items_with_refs[0]["source_ref_details"][0]["path"], source_ref)
            self.assertEqual(quality_checks["source_fact_coverage"]["status"], "pass")
            self.assertGreaterEqual(quality_checks["source_fact_coverage"]["details"]["matched_fact_count"], 1)

            manifest_view = asyncio.run(
                manager.view(
                    production_session_id=started.production_session_id,
                    view_type="manifest",
                    adk_state=state,
                )
            )
            manifest = manifest_view.view["manifest"]
            manifest_slides_with_refs = [slide for slide in manifest["slides"] if slide["source_refs"]]

            self.assertEqual(manifest["delivery"]["final_pptx_path"], metadata["delivery"]["final_pptx_path"])
            self.assertEqual(manifest["delivery"]["quality_status"], metadata["delivery"]["quality_status"])
            self.assertEqual(manifest["delivery"]["segment_count"], 3)
            self.assertTrue(manifest_slides_with_refs)
            self.assertEqual(manifest_slides_with_refs[0]["source_ref_details"][0]["name"], "acceptance_business.md")

            completed = asyncio.run(
                manager.resume(
                    production_session_id=started.production_session_id,
                    user_response={"decision": "approve"},
                    adk_state=state,
                )
            )
            completed_payload = _load_state_payload(completed)

        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed_payload["stage"], "completed")
        self.assertEqual(state["active_production_status"], "completed")
        self.assertEqual(state["active_production_sessions"]["ppt"]["status"], "completed")
        self.assertTrue(any(path.endswith("final.pptx") for path in state["final_file_paths"]))
        self.assertTrue(any(path.endswith("quality_report.md") for path in state["final_file_paths"]))
        self.assertTrue(any(path.endswith("render_manifest.md") for path in state["final_file_paths"]))
        self.assertFalse(any("/segments/" in path for path in state["final_file_paths"]))

    def test_manager_can_pause_at_brief_review_before_outline(self) -> None:
        state = _adk_state("session_ppt_brief_review")
        manager = _ppt_manager()

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
        manager = _ppt_manager()

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
        manager = _ppt_manager()

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

    def test_apply_revision_without_target_defaults_to_production_revision(self) -> None:
        state = _adk_state("session_ppt_revision_without_target")
        manager = _ppt_manager()

        started = asyncio.run(
            manager.start(
                user_prompt="做一份 3 页的产品策略更新。",
                input_files=[],
                placeholder_assets=False,
                render_settings={"target_pages": 3},
                adk_state=state,
            )
        )

        applied = asyncio.run(
            manager.apply_revision(
                production_session_id=started.production_session_id,
                user_response="请把整体语气改得更适合高管。",
                adk_state=state,
            )
        )
        payload = _load_state_payload(applied)

        self.assertIsNone(applied.error)
        self.assertEqual(applied.status, "needs_user_review")
        self.assertEqual(applied.stage, "outline_review")
        self.assertIn("Revision notes: 请把整体语气改得更适合高管。", payload["brief_summary"])
        self.assertEqual(payload["revision_history"][0]["notes"], "请把整体语气改得更适合高管。")
        self.assertEqual(payload["revision_history"][0]["user_response"]["notes"], "请把整体语气改得更适合高管。")
        self.assertIn("outline", payload["stale_items"])
        self.assertIn("deck_spec", payload["stale_items"])

    def test_apply_revision_updates_targeted_deck_slide_only(self) -> None:
        state = _adk_state("session_ppt_revision_deck_slide")
        manager = _ppt_manager()

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
        manager = _ppt_manager()

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
        self.assertEqual(regenerated.review_payload.items[0]["source_refs"], [])
        self.assertIn("deck_slide_status", regenerated.review_payload.items[0])
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
        manager = _ppt_manager()
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
        manager = _ppt_manager()
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
        manager = _ppt_manager()
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
        manager = _ppt_manager()

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
        manager = _ppt_manager()

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

        with patch("src.production.ppt.tool.PPTProductionManager", return_value=_ppt_manager()):
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
