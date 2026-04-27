"""PPTX template analysis for PPT production."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from src.production.ppt.models import IngestEntry, TemplateSummary
from src.runtime.workspace import resolve_workspace_path


_MAX_SAMPLE_TEXT = 8
_MAX_FONTS = 8
_MAX_COLORS = 10


class TemplateAnalyzerService:
    """Inspect a PPTX template without mutating it."""

    def build_summary(self, inputs: list[IngestEntry]) -> TemplateSummary:
        """Analyze the first valid PPT template input."""
        templates = [item for item in inputs if item.role == "template_pptx" and item.status == "valid"]
        if not templates:
            return TemplateSummary(status="not_started")

        primary = templates[0]
        path = resolve_workspace_path(primary.path)
        if path.suffix.lower() != ".pptx":
            return TemplateSummary(
                template_input_id=primary.input_id,
                summary=f"Template `{primary.name}` is not a PPTX package and cannot be analyzed in P1a.",
                status="unsupported",
                warnings=["Only `.pptx` template analysis is supported in P1a."],
            )
        if not path.is_file():
            return TemplateSummary(
                template_input_id=primary.input_id,
                summary=f"Template `{primary.name}` was not found in the workspace.",
                status="failed",
                warnings=["Template file was not found."],
            )

        try:
            return _analyze_pptx_template(primary, path)
        except Exception as exc:
            return TemplateSummary(
                template_input_id=primary.input_id,
                summary=f"Failed to analyze template `{primary.name}`.",
                status="failed",
                warnings=[f"Template analysis failed: {type(exc).__name__}: {exc}"],
            )


def _analyze_pptx_template(entry: IngestEntry, path: Path) -> TemplateSummary:
    with zipfile.ZipFile(path) as package:
        names = package.namelist()
        slide_files = _sorted_ooxml_parts(names, r"ppt/slides/slide(\d+)\.xml")
        layout_files = _sorted_ooxml_parts(names, r"ppt/slideLayouts/slideLayout(\d+)\.xml")
        master_files = _sorted_ooxml_parts(names, r"ppt/slideMasters/slideMaster(\d+)\.xml")
        theme_files = _sorted_ooxml_parts(names, r"ppt/theme/theme(\d+)\.xml")
        media_files = [name for name in names if name.startswith("ppt/media/")]

        sample_text = _sample_slide_text(package, slide_files)
        theme_xml = "\n".join(_read_package_text(package, name) for name in theme_files[:2])
        detected_fonts = _detected_fonts(theme_xml)
        detected_colors = _detected_colors(theme_xml)

    summary = (
        f"Analyzed template `{entry.name}` with {len(slide_files)} slide(s), "
        f"{len(layout_files)} layout(s), {len(master_files)} master(s), and {len(media_files)} media asset(s). "
        "P1a uses this as planning context; native PPTX generation remains active."
    )
    return TemplateSummary(
        template_input_id=entry.input_id,
        summary=summary,
        layout_count=len(layout_files),
        status="ready",
        warnings=["Template editing is not implemented in P1a; analysis is used only as planning context."],
        slide_count=len(slide_files),
        master_count=len(master_files),
        media_count=len(media_files),
        theme_count=len(theme_files),
        detected_fonts=detected_fonts,
        detected_colors=detected_colors,
        sample_text=sample_text,
        metadata={
            "template_name": entry.name,
            "slide_parts": slide_files[:20],
            "layout_parts": layout_files[:20],
        },
    )


def _sorted_ooxml_parts(names: list[str], pattern: str) -> list[str]:
    matcher = re.compile(pattern)
    matched = []
    for name in names:
        result = matcher.fullmatch(name)
        if result:
            matched.append((int(result.group(1)), name))
    return [name for _, name in sorted(matched)]


def _sample_slide_text(package: zipfile.ZipFile, slide_files: list[str]) -> list[str]:
    samples: list[str] = []
    for slide_file in slide_files[:5]:
        text = _extract_text_from_xml(_read_package_bytes(package, slide_file))
        for item in text:
            if item and item not in samples:
                samples.append(item[:160])
            if len(samples) >= _MAX_SAMPLE_TEXT:
                return samples
    return samples


def _extract_text_from_xml(payload: bytes) -> list[str]:
    root = ElementTree.fromstring(payload)
    values: list[str] = []
    for node in root.iter():
        if node.tag.endswith("}t") and node.text:
            value = re.sub(r"\s+", " ", node.text).strip()
            if value:
                values.append(value)
    return values


def _detected_fonts(theme_xml: str) -> list[str]:
    fonts: list[str] = []
    for font in re.findall(r'typeface="([^"]+)"', theme_xml):
        normalized = font.strip()
        if normalized and normalized not in fonts:
            fonts.append(normalized)
        if len(fonts) >= _MAX_FONTS:
            break
    return fonts


def _detected_colors(theme_xml: str) -> list[str]:
    colors: list[str] = []
    for color in re.findall(r'val="([0-9A-Fa-f]{6})"', theme_xml):
        normalized = color.upper()
        if normalized and normalized not in colors:
            colors.append(normalized)
        if len(colors) >= _MAX_COLORS:
            break
    return colors


def _read_package_bytes(package: zipfile.ZipFile, name: str) -> bytes:
    try:
        return package.read(name)
    except KeyError:
        return b""


def _read_package_text(package: zipfile.ZipFile, name: str) -> str:
    return _read_package_bytes(package, name).decode("utf-8", errors="ignore")
