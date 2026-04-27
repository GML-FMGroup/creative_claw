"""Preview rendering for PPT production outputs."""

from __future__ import annotations

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
        previews = self._render_with_office(pptx_path=pptx_path, deck_spec=deck_spec, output_dir=output_dir)
        if previews:
            return previews
        return self._render_fallback(deck_spec=deck_spec, render_settings=render_settings, output_dir=output_dir)

    def _render_with_office(self, *, pptx_path: str, deck_spec: DeckSpec, output_dir: Path) -> list[SlidePreview]:
        soffice = shutil.which("soffice")
        pdftoppm = shutil.which("pdftoppm")
        if not soffice or not pdftoppm:
            return []
        try:
            resolved_pptx = resolve_workspace_path(pptx_path)
            pdf_dir = output_dir / "pdf"
            pdf_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(pdf_dir), str(resolved_pptx)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )
            pdf_candidates = sorted(pdf_dir.glob("*.pdf"))
            if not pdf_candidates:
                return []
            prefix = output_dir / "slide"
            subprocess.run(
                [pdftoppm, "-png", "-r", "144", str(pdf_candidates[0]), str(prefix)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
            )
        except Exception:
            return []

        image_paths = sorted(output_dir.glob("slide-*.png"))
        previews: list[SlidePreview] = []
        for index, image_path in enumerate(image_paths, start=1):
            normalized_path = output_dir / f"slide-{index:02d}.png"
            if image_path != normalized_path:
                image_path.replace(normalized_path)
            slide_spec = deck_spec.slides[index - 1] if index - 1 < len(deck_spec.slides) else None
            previews.append(
                SlidePreview(
                    slide_id=slide_spec.slide_id if slide_spec is not None else f"slide_{index}",
                    sequence_index=index,
                    preview_path=workspace_relative_path(normalized_path),
                    metadata={"renderer": "soffice_pdftoppm"},
                )
            )
        return previews

    def _render_fallback(self, *, deck_spec: DeckSpec, render_settings: PPTRenderSettings, output_dir: Path) -> list[SlidePreview]:
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
                    metadata={"renderer": "pillow_fallback"},
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
    for font_name in ("Arial.ttf", "Aptos.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(font_name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text(text: str, *, max_chars: int) -> list[str]:
    words = str(text or "").split()
    if not words:
        return []
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
