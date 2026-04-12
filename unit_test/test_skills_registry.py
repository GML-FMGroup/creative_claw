import tempfile
import unittest
from pathlib import Path

from src.skills.registry import SkillRegistry


class SkillRegistryTests(unittest.TestCase):
    def test_builtin_skills_are_discovered(self) -> None:
        registry = SkillRegistry(workspace=Path("/tmp/nonexistent-creative-claw-workspace"))
        names = {item.name for item in registry.list_skills()}

        self.assertIn("code-edit", names)
        self.assertIn("minimax-cli-skill", names)
        self.assertIn("expert-usage-guide", names)
        self.assertIn("summarize", names)
        self.assertIn("web-research", names)
        self.assertIn("planning-with-files", names)

    def test_workspace_skill_overrides_builtin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            workspace = Path(tmpdir)
            skill_dir = workspace / "skills" / "code-edit"
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: code-edit\ndescription: custom code edit\n---\n\n# Custom Code Edit\n",
                encoding="utf-8",
            )

            registry = SkillRegistry(workspace=workspace)
            skills = {item.name: item for item in registry.list_skills()}

            self.assertEqual(skills["code-edit"].source, "workspace")
            self.assertIn("# Custom Code Edit", registry.read_skill("code-edit"))

    def test_read_skill_raises_for_missing_name(self) -> None:
        registry = SkillRegistry(workspace=Path("/tmp/nonexistent-creative-claw-workspace"))

        with self.assertRaises(ValueError):
            registry.read_skill("missing-skill")

    def test_summarize_skill_has_usable_metadata(self) -> None:
        registry = SkillRegistry(workspace=Path("/tmp/nonexistent-creative-claw-workspace"))
        skills = {item.name: item for item in registry.list_skills()}

        self.assertIn("summarize", skills)
        self.assertNotEqual(skills["summarize"].description, "summarize")
        self.assertIn("web pages", skills["summarize"].description)


if __name__ == "__main__":
    unittest.main()
