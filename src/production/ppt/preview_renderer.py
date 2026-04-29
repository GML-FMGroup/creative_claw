"""Preview rendering for PPT production outputs."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from src.production.ppt.models import DeckSpec, PPTRenderSettings, SlidePreview
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path


class PreviewRendererService:
    """Render PPTX previews with LibreOffice when possible and deterministic PNG fallback otherwise."""

    def render(
        self,
        *,
        pptx_path: str,
        deck_spec: DeckSpec,
        render_settings: PPTRenderSettings,
        output_dir: Path,
    ) -> list[SlidePreview]:
        """Render preview PNG files for a PPTX and return slide preview records."""
        output_dir.mkdir(parents=True, exist_ok=True)
        previews, fallback_reason = self._render_with_office(pptx_path=pptx_path, deck_spec=deck_spec, output_dir=output_dir)
        if previews:
            return previews
        return self._render_fallback(
            deck_spec=deck_spec,
            render_settings=render_settings,
            output_dir=output_dir,
            fallback_reason=fallback_reason,
        )

    def _render_with_office(self, *, pptx_path: str, deck_spec: DeckSpec, output_dir: Path) -> tuple[list[SlidePreview], str]:
        soffice = shutil.which("soffice")
        pdftoppm = shutil.which("pdftoppm")
        if not soffice:
            return [], "soffice_not_found"
        if not pdftoppm:
            return [], "pdftoppm_not_found"
        timeout_seconds = _render_timeout_seconds(deck_spec)
        try:
            resolved_pptx = resolve_workspace_path(pptx_path)
            pdf_dir = output_dir / "pdf"
            pdf_dir.mkdir(parents=True, exist_ok=True)
            _clear_previous_preview_images(output_dir)
            subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(pdf_dir), str(resolved_pptx)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
            )
            pdf_candidates = sorted(pdf_dir.glob("*.pdf"))
            if not pdf_candidates:
                return [], "soffice_pdf_missing"
            prefix = output_dir / "slide"
            subprocess.run(
                [pdftoppm, "-png", "-r", "144", str(pdf_candidates[0]), str(prefix)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return [], f"{_command_label(exc.cmd)}_timeout_after_{timeout_seconds}s"
        except subprocess.CalledProcessError as exc:
            return [], f"{_command_label(exc.cmd)}_failed:{_stderr_summary(exc.stderr)}"
        except Exception as exc:
            return [], f"office_preview_failed:{type(exc).__name__}"

        image_paths = _sorted_pdftoppm_images(output_dir)
        expected_count = len(deck_spec.slides)
        if expected_count and len(image_paths) != expected_count:
            return [], f"preview_image_count_mismatch:expected={expected_count},actual={len(image_paths)}"
        normalized_paths = _normalize_preview_image_names(image_paths, output_dir)
        previews: list[SlidePreview] = []
        for index, normalized_path in enumerate(normalized_paths, start=1):
            slide_spec = deck_spec.slides[index - 1] if index - 1 < len(deck_spec.slides) else None
            previews.append(
                SlidePreview(
                    slide_id=slide_spec.slide_id if slide_spec is not None else f"slide_{index}",
                    sequence_index=index,
                    preview_path=workspace_relative_path(normalized_path),
                    metadata={"renderer": "soffice_pdftoppm"},
                )
            )
        return previews, ""

    def _render_fallback(
        self,
        *,
        deck_spec: DeckSpec,
        render_settings: PPTRenderSettings,
        output_dir: Path,
        fallback_reason: str = "",
    ) -> list[SlidePreview]:
        width, height = _pixel_size(render_settings.aspect_ratio)
        theme = _fallback_theme(render_settings.style_preset)
        title_font = _load_font(44)
        body_font = _load_font(24)
        small_font = _load_font(18)
        previews: list[SlidePreview] = []
        for slide_spec in deck_spec.slides:
            path = output_dir / f"slide-{slide_spec.sequence_index:02d}.png"
            image = Image.new("RGB", (width, height), theme["background"])
            draw = ImageDraw.Draw(image)
            draw.rectangle([0, 0, 26, height], fill=theme["accent"])
            draw.text((64, 58), slide_spec.title[:80], fill=theme["title"], font=title_font)
            y = 160
            for bullet in slide_spec.bullets[:6]:
                for line in _wrap_text(bullet, max_chars=52 if width > height else 32):
                    draw.text((86, y), line, fill=theme["body"], font=body_font)
                    y += 34
                y += 12
            if slide_spec.visual_notes:
                draw.text((64, height - 82), slide_spec.visual_notes[:120], fill=theme["muted"], font=small_font)
            draw.text((width - 86, height - 48), f"{slide_spec.sequence_index:02d}", fill=theme["muted"], font=small_font)
            image.save(path)
            previews.append(
                SlidePreview(
                    slide_id=slide_spec.slide_id,
                    sequence_index=slide_spec.sequence_index,
                    preview_path=workspace_relative_path(path),
                    metadata={
                        "renderer": "pillow_fallback",
                        "fallback_reason": fallback_reason or "office_preview_unavailable",
                    },
                )
            )
        return previews


def _pixel_size(aspect_ratio: str) -> tuple[int, int]:
    if aspect_ratio == "4:3":
        return 1024, 768
    if aspect_ratio == "9:16":
        return 720, 1280
    return 1280, 720


def _fallback_theme(style_preset: str) -> dict[str, tuple[int, int, int]]:
    if style_preset == "editorial_visual":
        return {"background": (247, 237, 226), "accent": (184, 80, 66), "title": (43, 27, 23), "body": (36, 23, 19), "muted": (107, 91, 84)}
    if style_preset == "pitch_deck":
        return {"background": (244, 239, 231), "accent": (216, 160, 61), "title": (16, 40, 32), "body": (23, 32, 26), "muted": (75, 96, 84)}
    return {"background": (246, 248, 254), "accent": (249, 177, 21), "title": (30, 39, 97), "body": (24, 32, 51), "muted": (82, 100, 140)}


def _load_font(size: int) -> ImageFont.ImageFont:
    for font_name in (
        "Arial.ttf",
        "Aptos.ttf",
        "Helvetica.ttc",
        "DejaVuSans.ttf",
        "NotoSansCJK-Regular.ttc",
        "NotoSansCJKsc-Regular.otf",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ):
        try:
            return ImageFont.truetype(font_name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_timeout_seconds(deck_spec: DeckSpec) -> int:
    """Return a render timeout scaled for larger decks."""
    return max(60, 5 * max(1, len(deck_spec.slides)))


def _clear_previous_preview_images(output_dir: Path) -> None:
    """Remove stale pdftoppm image outputs from a previous render attempt."""
    for image_path in output_dir.glob("slide-*.png"):
        image_path.unlink(missing_ok=True)


def _sorted_pdftoppm_images(output_dir: Path) -> list[Path]:
    """Return pdftoppm output images sorted by numeric page index."""
    numbered_paths: list[tuple[int, Path]] = []
    for image_path in output_dir.glob("slide-*.png"):
        page_number = _pdftoppm_page_number(image_path)
        if page_number is not None:
            numbered_paths.append((page_number, image_path))
    return [image_path for _, image_path in sorted(numbered_paths, key=lambda item: item[0])]


def _pdftoppm_page_number(image_path: Path) -> int | None:
    """Extract the numeric page suffix from one pdftoppm output path."""
    match = re.search(r"-(\d+)\.png$", image_path.name)
    return int(match.group(1)) if match else None


def _normalize_preview_image_names(image_paths: list[Path], output_dir: Path) -> list[Path]:
    """Rename preview images to stable two-digit sequence paths without collisions."""
    temp_paths: list[Path] = []
    for index, image_path in enumerate(image_paths, start=1):
        temp_path = output_dir / f".slide-normalized-{index:02d}.png"
        image_path.replace(temp_path)
        temp_paths.append(temp_path)

    normalized_paths: list[Path] = []
    for index, temp_path in enumerate(temp_paths, start=1):
        normalized_path = output_dir / f"slide-{index:02d}.png"
        temp_path.replace(normalized_path)
        normalized_paths.append(normalized_path)
    return normalized_paths


def _command_label(command: object) -> str:
    """Return a compact command label for preview-render diagnostics."""
    if isinstance(command, (list, tuple)) and command:
        return Path(str(command[0])).name or "preview_command"
    return "preview_command"


def _stderr_summary(stderr: bytes | str | None) -> str:
    """Return a bounded stderr summary for preview-render metadata."""
    if stderr is None:
        return "no_stderr"
    text = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else str(stderr)
    text = " ".join(text.split())
    return text[:180] or "no_stderr"


def _wrap_text(text: str, *, max_chars: int) -> list[str]:
    words = str(text or "").split()
    if not words:
        return []
    if len(words) == 1 and len(words[0]) > max_chars:
        return [words[0][index:index + max_chars] for index in range(0, len(words[0]), max_chars)]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + len(word) + 1 > max_chars:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}"
    lines.append(current)
    return lines
