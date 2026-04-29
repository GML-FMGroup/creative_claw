"""Static validation for generated Design HTML artifacts."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from src.production.design.models import HtmlValidationReport
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path


_CSS_URL_PATTERN = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""", re.IGNORECASE)
_WINDOWS_ABSOLUTE_PATTERN = re.compile(r"[A-Za-z]:\\")
_LOCAL_PATH_MARKERS = ("/Users/", "/home/", "/tmp/", "/var/folders/")
_RESOURCE_SRC_TAGS = {"img", "source", "video", "audio", "iframe", "embed", "script", "track"}
_RESOURCE_LINK_RELS = {"stylesheet", "preload", "modulepreload", "icon", "apple-touch-icon"}
_DANGEROUS_SCRIPT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("eval(", re.compile(r"\beval\s*\(", re.IGNORECASE)),
    ("document.write(", re.compile(r"\bdocument\s*\.\s*write\s*\(", re.IGNORECASE)),
    ("Function(", re.compile(r"(?<![\w$])(?:new\s+)?Function\s*\(")),
    ("new function(", re.compile(r"\bnew\s+function\s*\(", re.IGNORECASE)),
    ("setTimeout(string)", re.compile(r"\bsetTimeout\s*\(\s*['\"]", re.IGNORECASE)),
    ("setInterval(string)", re.compile(r"\bsetInterval\s*\(\s*['\"]", re.IGNORECASE)),
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

        context = _parse_html_validation_context(text)
        local_references = _find_local_absolute_references(context)
        if local_references:
            issues.append(
                "HTML contains a local absolute path or file URL: "
                + ", ".join(local_references[:3])
            )

        for issue in _validate_script_safety(context):
            issues.append(issue)

        duplicate_ids = _find_duplicate_ids(context.ids)
        if duplicate_ids:
            issues.append(f"HTML contains duplicate id values: {', '.join(sorted(duplicate_ids)[:5])}")

        resource_issues = _validate_resource_references(
            context,
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


class _HtmlValidationContext(HTMLParser):
    """Collect URL, script, style, and id facts needed by static validation."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.url_attrs: list[tuple[str, str, str]] = []
        self.resource_refs: list[tuple[str, str, str]] = []
        self.script_texts: list[str] = []
        self.style_texts: list[str] = []
        self._script_depth = 0
        self._style_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(tag, attrs)
        normalized_tag = tag.lower()
        if normalized_tag == "script":
            self._script_depth += 1
        elif normalized_tag == "style":
            self._style_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag == "script" and self._script_depth:
            self._script_depth -= 1
        elif normalized_tag == "style" and self._style_depth:
            self._style_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._script_depth:
            self.script_texts.append(data)
        if self._style_depth:
            self.style_texts.append(data)

    def _handle_tag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        normalized_attrs = {name.lower(): value or "" for name, value in attrs}
        if normalized_attrs.get("id"):
            self.ids.append(normalized_attrs["id"].strip())
        for attr_name, attr_value in normalized_attrs.items():
            if attr_name in {"src", "href", "poster", "data"}:
                self.url_attrs.append((normalized_tag, attr_name, attr_value.strip()))
            if attr_name.startswith("on") and attr_value:
                self.script_texts.append(attr_value)
            if attr_name == "style" and attr_value:
                self.style_texts.append(attr_value)

        if normalized_tag in _RESOURCE_SRC_TAGS and normalized_attrs.get("src"):
            self.resource_refs.append((normalized_tag, "src", normalized_attrs["src"].strip()))
        if normalized_tag == "object" and normalized_attrs.get("data"):
            self.resource_refs.append((normalized_tag, "data", normalized_attrs["data"].strip()))
        if normalized_tag == "video" and normalized_attrs.get("poster"):
            self.resource_refs.append((normalized_tag, "poster", normalized_attrs["poster"].strip()))
        if normalized_tag == "link" and normalized_attrs.get("href"):
            rel_values = set(normalized_attrs.get("rel", "").lower().split())
            if rel_values & _RESOURCE_LINK_RELS:
                self.resource_refs.append((normalized_tag, "href", normalized_attrs["href"].strip()))


def _parse_html_validation_context(text: str) -> _HtmlValidationContext:
    """Parse generated HTML into a validation context."""
    parser = _HtmlValidationContext()
    parser.feed(text)
    parser.close()
    return parser


def _find_local_absolute_references(context: _HtmlValidationContext) -> list[str]:
    """Return local absolute references from URL-bearing HTML/CSS locations."""
    references = [value for _tag, _attr, value in context.url_attrs]
    references.extend(_css_url_references(context))
    result: list[str] = []
    for ref in references:
        if _is_local_absolute_reference(ref):
            result.append(ref)
    return result


def _is_local_absolute_reference(ref: str) -> bool:
    value = str(ref or "").strip()
    if not value:
        return False
    if value.lower().startswith("file://"):
        return True
    if any(marker in value for marker in _LOCAL_PATH_MARKERS):
        return True
    return bool(_WINDOWS_ABSOLUTE_PATTERN.search(value))


def _validate_script_safety(context: _HtmlValidationContext) -> list[str]:
    """Return script safety issues from script-like locations only."""
    issues: list[str] = []
    script_text = "\n".join(context.script_texts)
    for label, pattern in _DANGEROUS_SCRIPT_PATTERNS:
        if pattern.search(script_text):
            issues.append(f"HTML contains disallowed script pattern: {label}")
    for tag, attr, value in context.url_attrs:
        if value.strip().lower().startswith("javascript:"):
            issues.append(f"HTML contains disallowed javascript URL in {tag}.{attr}.")
    return issues


def _find_duplicate_ids(ids: list[str]) -> set[str]:
    seen: set[str] = set()
    duplicate: set[str] = set()
    for value in ids:
        if value in seen:
            duplicate.add(value)
        seen.add(value)
    return duplicate


def _validate_resource_references(context: _HtmlValidationContext, *, html_dir: Path, session_root: Path) -> list[str]:
    issues: list[str] = []
    references = list(context.resource_refs)
    references.extend(("style", "url", ref) for ref in _css_url_references(context))
    for tag, attr, ref in references:
        if not ref or ref.startswith(("#", "data:", "mailto:", "tel:", "javascript:")):
            continue
        parsed = urlparse(ref)
        if parsed.scheme in {"http", "https"} or parsed.netloc:
            issues.append(f"External runtime resource is not allowed in {tag}.{attr}: `{ref}`.")
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


def _css_url_references(context: _HtmlValidationContext) -> list[str]:
    """Return CSS url() references from style tags and style attributes."""
    references: list[str] = []
    for style_text in context.style_texts:
        references.extend(match.group(1).strip() for match in _CSS_URL_PATTERN.finditer(style_text))
    return references
