"""Regression tests for the Design P1 completion record."""

from pathlib import Path
import unittest


class DesignP1CompletionDocTests(unittest.TestCase):
    """Validate that the P1 closeout doc covers the shipped acceptance scope."""

    def setUp(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        self.doc_text = (
            repo_root / "docs" / "design_p1_completion_zh.md"
        ).read_text(encoding="utf-8")

    def test_completion_doc_covers_design_p1_slices(self) -> None:
        for marker in (
            "P1a",
            "P1b",
            "P1c",
            "P1d",
            "P1e",
            "P1f",
            "P1g",
            "P1h",
            "P1i",
            "P1j",
            "P1k",
            "P1l",
            "P1m",
            "P1n",
            "P1o",
            "P1p",
            "P1q",
            "P1r",
        ):
            self.assertIn(marker, self.doc_text)

    def test_completion_doc_covers_user_visible_artifacts(self) -> None:
        for marker in (
            "run_design_production",
            "design_direction_review",
            "preview_review",
            "multi_html",
            "design_handoff_bundle.zip",
            "design_tokens.json",
            "design_tokens.css",
            "design_system_audit",
            "component_inventory",
            "browser_diagnostics",
            "artifact_lineage",
            "accessibility_report",
            "design_system_extraction",
            "page_handoff",
            "strict ADK schemas",
        ):
            self.assertIn(marker, self.doc_text)

    def test_completion_doc_covers_acceptance_and_p2_boundary(self) -> None:
        for marker in (
            ".venv/bin/python -m unittest discover unit_test",
            ".venv/bin/adk eval tests/eval/creative_claw_orchestrator",
            "start_multi_page_microsite_preserves_pages",
            "git diff --check",
            "Section-level regeneration",
            "Figma / code handoff",
            "Asset pipeline",
            "P2",
        ):
            self.assertIn(marker, self.doc_text)
        self.assertNotIn("/Users/", self.doc_text)


if __name__ == "__main__":
    unittest.main()
