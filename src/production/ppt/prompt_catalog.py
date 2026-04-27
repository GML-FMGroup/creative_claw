"""Prompt-template loading for PPT production."""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources
from typing import Any, Mapping


class PPTPromptCatalogError(ValueError):
    """Raised when a PPT prompt template cannot be rendered safely."""


_PACKAGE = "src.production.ppt.prompts"
_TEMPLATE_NAME_RE = re.compile(r"^[a-z0-9_]+$")
_PLACEHOLDER_RE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


def available_prompt_templates() -> list[str]:
    """Return packaged PPT prompt template names."""
    prompt_dir = resources.files(_PACKAGE)
    return sorted(
        item.name.removesuffix(".md")
        for item in prompt_dir.iterdir()
        if item.name.endswith(".md")
    )


def render_prompt_template(template_name: str, variables: Mapping[str, Any]) -> str:
    """Render a packaged PPT prompt template with strict variable validation."""
    template = _load_prompt_template(template_name)
    required = set(_PLACEHOLDER_RE.findall(template))
    missing = sorted(name for name in required if name not in variables)
    if missing:
        raise PPTPromptCatalogError(
            f"Prompt template `{template_name}` is missing variables: {', '.join(missing)}"
        )
    rendered = _PLACEHOLDER_RE.sub(lambda match: str(variables[match.group(1)]), template)
    return _normalize_rendered_prompt(rendered)


@lru_cache(maxsize=64)
def _load_prompt_template(template_name: str) -> str:
    """Load one package-local prompt template by safe template name."""
    normalized = str(template_name or "").strip()
    if not _TEMPLATE_NAME_RE.fullmatch(normalized):
        raise PPTPromptCatalogError(f"Invalid prompt template name: {template_name!r}")
    prompt_path = resources.files(_PACKAGE).joinpath(f"{normalized}.md")
    if not prompt_path.is_file():
        raise PPTPromptCatalogError(f"Unknown prompt template: {normalized}")
    return _strip_frontmatter(prompt_path.read_text(encoding="utf-8"))


def _strip_frontmatter(template: str) -> str:
    """Remove optional Markdown frontmatter before rendering."""
    text = template.lstrip()
    if not text.startswith("---\n"):
        return text
    _, separator, body = text.partition("\n---\n")
    return body if separator else template


def _normalize_rendered_prompt(prompt: str) -> str:
    """Normalize prompt whitespace while preserving paragraph boundaries."""
    lines = [line.rstrip() for line in str(prompt or "").strip().splitlines()]
    normalized_lines: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line.strip()
        if blank and previous_blank:
            continue
        normalized_lines.append(line)
        previous_blank = blank
    return "\n".join(normalized_lines).strip()
