"""Markdown skill discovery for Creative Claw."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from conf.system import SYS_CONFIG


@dataclass(frozen=True, slots=True)
class SkillInfo:
    """Metadata for one discovered skill."""

    name: str
    path: Path
    source: str
    description: str


class SkillRegistry:
    """Discover skills from workspace and built-in directories."""

    def __init__(
        self,
        workspace: Path | None = None,
        builtin_skills_dir: Path | None = None,
    ) -> None:
        cwd = Path.cwd() if workspace is None else workspace
        self.workspace = cwd.resolve()
        self.workspace_skills_dir = self.workspace / "skills"
        self.builtin_skills_dir = (
            Path(SYS_CONFIG.base_dir) / "skills"
            if builtin_skills_dir is None
            else builtin_skills_dir
        ).resolve()

    def list_skills(self) -> list[SkillInfo]:
        """Return discovered skills, with workspace skills overriding built-ins."""
        discovered: dict[str, SkillInfo] = {}

        for info in self._scan(self.workspace_skills_dir, source="workspace"):
            discovered[info.name] = info

        for info in self._scan(self.builtin_skills_dir, source="builtin"):
            if info.name not in discovered:
                discovered[info.name] = info

        return sorted(discovered.values(), key=lambda item: item.name.lower())

    def read_skill(self, name: str) -> str:
        """Read the full markdown content of one skill."""
        skill_name = name.strip()
        if not skill_name:
            raise ValueError("Skill name cannot be empty.")

        for info in self.list_skills():
            if info.name == skill_name:
                return info.path.read_text(encoding="utf-8")

        raise ValueError(f"Skill '{skill_name}' not found.")

    def build_summary(self) -> str:
        """Build an XML-like skill summary for prompt injection."""
        lines = ["<skills>"]
        for info in self.list_skills():
            lines.append("  <skill>")
            lines.append(f"    <name>{_xml_escape(info.name)}</name>")
            lines.append(f"    <description>{_xml_escape(info.description)}</description>")
            lines.append(f"    <source>{info.source}</source>")
            lines.append("  </skill>")
        lines.append("</skills>")
        return "\n".join(lines)

    def _scan(self, skills_dir: Path, source: str) -> list[SkillInfo]:
        if not skills_dir.exists() or not skills_dir.is_dir():
            return []

        result: list[SkillInfo] = []
        for child in skills_dir.iterdir():
            if not child.is_dir():
                continue
            skill_file = child / "SKILL.md"
            if not skill_file.is_file():
                continue
            result.append(
                SkillInfo(
                    name=child.name,
                    path=skill_file.resolve(),
                    source=source,
                    description=self._extract_description(skill_file) or child.name,
                )
            )
        return result

    @staticmethod
    def _extract_description(skill_file: Path) -> str | None:
        content = skill_file.read_text(encoding="utf-8")
        if not content.startswith("---"):
            return None

        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return None

        for line in match.group(1).splitlines():
            if not line.strip().startswith("description:"):
                continue
            _, value = line.split(":", 1)
            return value.strip().strip("\"'")
        return None


def get_skill_registry() -> SkillRegistry:
    """Build a registry from optional environment overrides."""
    workspace_env = os.getenv("CREATIVE_CLAW_WORKSPACE")
    builtin_env = os.getenv("CREATIVE_CLAW_BUILTIN_SKILLS_DIR")
    workspace = Path(workspace_env).expanduser() if workspace_env else None
    builtin_skills_dir = Path(builtin_env).expanduser() if builtin_env else None
    return SkillRegistry(workspace=workspace, builtin_skills_dir=builtin_skills_dir)


def _xml_escape(text: str) -> str:
    """Escape user-facing values before injecting into XML-like text."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
