"""Browser preview rendering for generated Design HTML artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.production.design.browser_environment import (
    browser_environment_metadata,
    browser_runtime_issue,
    playwright_package_missing_issue,
)
from src.production.design.models import PreviewReport, ViewportSpec
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path


_DEFAULT_VIEWPORTS = [
    ViewportSpec(name="desktop", width=1440, height=1000),
    ViewportSpec(name="mobile", width=390, height=844),
]


class HtmlPreviewRenderer:
    """Render HTML in a browser and capture screenshots plus console reports."""

    async def render(
        self,
        *,
        artifact_id: str,
        html_path: str | Path,
        output_dir: Path,
        viewports: list[ViewportSpec] | None = None,
    ) -> list[PreviewReport]:
        """Render one HTML file for each viewport, returning reports or warnings."""
        resolved_html_path = resolve_workspace_path(html_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        selected_viewports = viewports or _DEFAULT_VIEWPORTS
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            reason = playwright_package_missing_issue(exc)
            return [
                _preview_unavailable_report(
                    artifact_id=artifact_id,
                    viewport=viewport,
                    reason=reason,
                )
                for viewport in selected_viewports
            ]

        reports: list[PreviewReport] = []
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch()
                try:
                    for viewport in selected_viewports:
                        reports.append(
                            await _render_one_viewport(
                                playwright_browser=browser,
                                artifact_id=artifact_id,
                                html_path=resolved_html_path,
                                output_dir=output_dir,
                                viewport=viewport,
                            )
                        )
                finally:
                    await browser.close()
        except Exception as exc:
            reason = browser_runtime_issue(context="preview rendering", exc=exc)
            reports = [
                _preview_unavailable_report(
                    artifact_id=artifact_id,
                    viewport=viewport,
                    reason=reason,
                )
                for viewport in selected_viewports
            ]
        return reports


async def _render_one_viewport(
    *,
    playwright_browser: Any,
    artifact_id: str,
    html_path: Path,
    output_dir: Path,
    viewport: ViewportSpec,
) -> PreviewReport:
    console_errors: list[str] = []
    network_failures: list[str] = []
    page = await playwright_browser.new_page(viewport={"width": viewport.width, "height": viewport.height})
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("requestfailed", lambda request: network_failures.append(request.url))
    screenshot_path = output_dir / f"{html_path.stem}_{viewport.name}.png"
    issues: list[str] = []
    metrics: dict[str, Any] = {}
    try:
        await page.goto(html_path.as_uri(), wait_until="networkidle")
        await page.screenshot(path=str(screenshot_path), full_page=True)
        metrics = await page.evaluate(
            """() => ({
                bodyScrollWidth: document.body ? document.body.scrollWidth : 0,
                documentClientWidth: document.documentElement ? document.documentElement.clientWidth : 0,
                bodyScrollHeight: document.body ? document.body.scrollHeight : 0,
                viewportWidth: window.innerWidth,
                viewportHeight: window.innerHeight
            })"""
        )
        if int(metrics.get("bodyScrollHeight") or 0) <= 0:
            issues.append("Rendered body height is zero.")
        if int(metrics.get("bodyScrollWidth") or 0) > int(metrics.get("viewportWidth") or viewport.width) + 8:
            issues.append("Rendered page has horizontal overflow.")
        if not screenshot_path.exists() or screenshot_path.stat().st_size <= 0:
            issues.append("Screenshot file is empty.")
    finally:
        await page.close()

    return PreviewReport(
        artifact_id=artifact_id,
        viewport=viewport.name,
        screenshot_path=workspace_relative_path(screenshot_path) if screenshot_path.exists() else "",
        console_errors=console_errors,
        network_failures=network_failures,
        layout_metrics=metrics,
        valid=not issues and not console_errors and not network_failures,
        issues=issues,
    )


def _preview_unavailable_report(*, artifact_id: str, viewport: ViewportSpec, reason: str) -> PreviewReport:
    """Build a warning-style preview report when browser rendering cannot run."""
    metrics: dict[str, Any] = {
        "width": viewport.width,
        "height": viewport.height,
        "preview": "unavailable",
    }
    metrics.update(browser_environment_metadata(reason))
    return PreviewReport(
        artifact_id=artifact_id,
        viewport=viewport.name,
        valid=False,
        issues=[reason],
        layout_metrics=metrics,
    )
