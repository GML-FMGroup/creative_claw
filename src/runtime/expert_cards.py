"""Markdown expert cards used to document and describe expert agents."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_CARD_DELIMITER = "+++"
_DEFAULT_EXPERTS_ROOT = Path(__file__).resolve().parents[2] / "src" / "agents" / "experts"
_DESCRIPTION_SECTIONS = (
    "When to Use",
    "Routing Notes",
    "Provider Boundaries",
    "When Not to Use",
)


@dataclass(frozen=True, slots=True)
class ExpertCard:
    """One parsed markdown expert card."""

    name: str
    path: Path
    metadata: dict[str, Any]
    body: str

    def build_description(self) -> str:
        """Return a compact one-line description suitable for orchestrator prompts."""
        sections = _extract_markdown_sections(self.body, _DESCRIPTION_SECTIONS)
        raw_description = "\n".join(sections) if sections else self.body
        return _normalize_markdown_text(raw_description)


def parse_expert_card(path: Path) -> ExpertCard:
    """Parse one `EXPERT.md` file with TOML frontmatter."""
    raw_text = path.read_text(encoding="utf-8")
    metadata, body = _split_toml_frontmatter(raw_text, path=path)
    name = str(metadata.get("name", "")).strip()
    if not name:
        raise ValueError(f"Expert card {path} must define frontmatter field `name`.")
    return ExpertCard(name=name, path=path, metadata=metadata, body=body.strip())


def discover_expert_cards(experts_root: Path | None = None) -> dict[str, ExpertCard]:
    """Return parsed expert cards keyed by expert agent name."""
    root = experts_root or _DEFAULT_EXPERTS_ROOT
    cards: dict[str, ExpertCard] = {}
    if not root.exists():
        return cards

    for path in sorted(root.glob("**/EXPERT.md")):
        card = parse_expert_card(path)
        cards[card.name] = card
    return cards


def _split_toml_frontmatter(raw_text: str, *, path: Path) -> tuple[dict[str, Any], str]:
    """Split TOML frontmatter from markdown body."""
    lines = raw_text.splitlines()
    if not lines or lines[0].strip() != _CARD_DELIMITER:
        raise ValueError(f"Expert card {path} must start with `{_CARD_DELIMITER}` frontmatter.")

    try:
        closing_index = next(
            index for index, line in enumerate(lines[1:], start=1) if line.strip() == _CARD_DELIMITER
        )
    except StopIteration as exc:
        raise ValueError(f"Expert card {path} is missing closing `{_CARD_DELIMITER}`.") from exc

    frontmatter = "\n".join(lines[1:closing_index])
    body = "\n".join(lines[closing_index + 1 :])
    metadata = tomllib.loads(frontmatter)
    return dict(metadata), body


def _extract_markdown_sections(body: str, section_names: tuple[str, ...]) -> list[str]:
    """Extract selected second-level markdown sections by title."""
    sections: dict[str, list[str]] = {}
    current_title = ""
    for line in body.splitlines():
        heading_match = re.match(r"^##\s+(.+?)\s*$", line)
        if heading_match:
            current_title = heading_match.group(1).strip()
            sections.setdefault(current_title, [])
            continue
        if current_title:
            sections.setdefault(current_title, []).append(line)

    return [
        "\n".join(sections[section_name]).strip()
        for section_name in section_names
        if "\n".join(sections.get(section_name, [])).strip()
    ]


def _normalize_markdown_text(value: str) -> str:
    """Normalize markdown prose into one compact prompt-friendly line."""
    lines: list[str] = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^[-*]\s+", "", line)
        lines.append(line)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()
