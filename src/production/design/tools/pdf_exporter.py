"""PDF export for generated Design HTML artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from src.production.design.models import PdfExportReport
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path


class HtmlPdfExporter:
    """Export an HTML artifact to PDF using browser print support."""

    async def export(
        self,
        *,
        artifact_id: str,
        html_path: str | Path,
        output_path: Path,
    ) -> PdfExportReport:
        """Export one HTML artifact to PDF, returning a non-throwing report."""
        source_html_path = str(html_path)
        try:
            resolved_html_path = resolve_workspace_path(html_path)
        except ValueError as exc:
            return _pdf_export_report(
                artifact_id=artifact_id,
                source_html_path=source_html_path,
                status="failed",
                issue=f"HTML path is outside the workspace: {exc}",
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            return _pdf_export_report(
                artifact_id=artifact_id,
                source_html_path=source_html_path,
                status="unavailable",
                issue=f"Playwright is not available: {type(exc).__name__}",
            )

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    page = await browser.new_page()
                    try:
                        await page.goto(resolved_html_path.as_uri(), wait_until="networkidle")
                        await page.pdf(path=str(output_path), format="A4", print_background=True)
                    finally:
                        await page.close()
                finally:
                    await browser.close()
        except Exception as exc:
            return _pdf_export_report(
                artifact_id=artifact_id,
                source_html_path=source_html_path,
                status="failed",
                issue=f"PDF export failed: {type(exc).__name__}: {exc}",
            )

        if not output_path.exists() or output_path.stat().st_size <= 0:
            return _pdf_export_report(
                artifact_id=artifact_id,
                source_html_path=source_html_path,
                status="failed",
                issue="PDF export produced an empty file.",
            )
        return PdfExportReport(
            artifact_id=artifact_id,
            source_html_path=source_html_path,
            pdf_path=workspace_relative_path(output_path),
            status="exported",
        )


def _pdf_export_report(
    *,
    artifact_id: str,
    source_html_path: str,
    status: Literal["unavailable", "failed"],
    issue: str,
) -> PdfExportReport:
    return PdfExportReport(
        artifact_id=artifact_id,
        source_html_path=source_html_path,
        status=status,
        issues=[issue],
    )
