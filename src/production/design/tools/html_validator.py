"""Static validation for generated Design HTML artifacts."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from src.production.design.models import HtmlValidationReport
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path


_RESOURCE_PATTERN = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_CSS_URL_PATTERN = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""", re.IGNORECASE)
_ID_PATTERN = re.compile(r"""\bid\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_DANGEROUS_SCRIPT_PATTERNS = (
    "eval(",
    "document.write(",
    "new function(",
)


class HtmlValidator:
    """Validate generated HTML before browser preview."""

    def validate(self, html_path: str | Path, *, session_root: Path, artifact_id: str) -> HtmlValidationReport:
        """Validate file existence, local references, and basic HTML safety."""
        issues: list[str] = []
        warnings: list[str] = []
        try:
            resolved_html_path = resolve_workspace_path(html_path)
        except Exception as exc:
            return HtmlValidationReport(
                artifact_id=artifact_id,
                path=str(html_path),
                status="invalid",
                issues=[f"HTML path is not inside the workspace: {type(exc).__name__}"],
            )

        if not resolved_html_path.exists() or not resolved_html_path.is_file():
            issues.append("HTML file does not exist.")
            return HtmlValidationReport(
                artifact_id=artifact_id,
                path=workspace_relative_path(resolved_html_path),
                status="invalid",
                issues=issues,
            )

        text = resolved_html_path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            issues.append("HTML file is empty.")
        if len(text.encode("utf-8")) > 2_000_000:
            warnings.append("HTML file is larger than the recommended P0 size.")

        lowered = text.lower()
        if "<html" not in lowered or "</html>" not in lowered:
            issues.append("HTML document must include an html root element.")
        if "<body" not in lowered or "</body>" not in lowered:
            issues.append("HTML document must include a body element.")
        if "<style" not in lowered:
            warnings.append("No inline style block found; P0 expects self-contained HTML/CSS.")

        if _contains_local_absolute_reference(text):
            issues.append("HTML contains a local absolute path or file URL.")

        for pattern in _DANGEROUS_SCRIPT_PATTERNS:
            if pattern in lowered:
                issues.append(f"HTML contains disallowed script pattern: {pattern}")

        duplicate_ids = _find_duplicate_ids(text)
        if duplicate_ids:
            issues.append(f"HTML contains duplicate id values: {', '.join(sorted(duplicate_ids)[:5])}")

        resource_issues = _validate_local_resources(
            text,
            html_dir=resolved_html_path.parent,
            session_root=session_root,
        )
        issues.extend(resource_issues)

        return HtmlValidationReport(
            artifact_id=artifact_id,
            path=workspace_relative_path(resolved_html_path),
            status="invalid" if issues else "valid",
            issues=issues,
            warnings=warnings,
        )


def _contains_local_absolute_reference(text: str) -> bool:
    """Return whether HTML contains local absolute references."""
    if "file://" in text.lower():
        return True
    local_markers = ("/Users/", "/home/", "/tmp/", "/var/folders/")
    if any(marker in text for marker in local_markers):
        return True
    return bool(re.search(r"[A-Za-z]:\\", text))


def _find_duplicate_ids(text: str) -> set[str]:
    ids = [match.group(1).strip() for match in _ID_PATTERN.finditer(text)]
    seen: set[str] = set()
    duplicate: set[str] = set()
    for value in ids:
        if value in seen:
            duplicate.add(value)
        seen.add(value)
    return duplicate


def _validate_local_resources(text: str, *, html_dir: Path, session_root: Path) -> list[str]:
    issues: list[str] = []
    references = [match.group(1).strip() for match in _RESOURCE_PATTERN.finditer(text)]
    references.extend(match.group(1).strip() for match in _CSS_URL_PATTERN.finditer(text))
    for ref in references:
        if not ref or ref.startswith(("#", "data:", "mailto:", "tel:", "javascript:")):
            continue
        parsed = urlparse(ref)
        if parsed.scheme in {"http", "https"}:
            continue
        if parsed.scheme:
            issues.append(f"Unsupported resource URL scheme for `{ref}`.")
            continue
        if Path(ref).is_absolute():
            issues.append(f"Resource reference must be workspace-relative, not absolute: `{ref}`.")
            continue
        target = (html_dir / parsed.path).resolve()
        try:
            target.relative_to(session_root.resolve())
        except ValueError:
            issues.append(f"Resource reference escapes the production session: `{ref}`.")
            continue
        if not target.exists():
            issues.append(f"Referenced local resource does not exist: `{ref}`.")
    return issues

