import os
import tempfile
import unittest
from pathlib import Path

from src.tools.builtin_tools import BuiltinToolbox, edit_file, exec_command, list_dir, read_file, web_fetch, write_file


class BuiltinToolTests(unittest.TestCase):
    def test_read_write_and_edit_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            previous = os.environ.get("CREATIVE_CLAW_WORKSPACE")
            os.environ["CREATIVE_CLAW_WORKSPACE"] = str(root)
            try:
                write_result = write_file("demo.txt", "hello world")
                read_result = read_file("demo.txt")
                edit_result = edit_file("demo.txt", "world", "creative_claw")

                self.assertIn("Successfully wrote", write_result)
                self.assertEqual(read_result, "hello world")
                self.assertIn("Successfully edited", edit_result)
                self.assertEqual(read_file("demo.txt"), "hello creative_claw")
            finally:
                if previous is None:
                    os.environ.pop("CREATIVE_CLAW_WORKSPACE", None)
                else:
                    os.environ["CREATIVE_CLAW_WORKSPACE"] = previous

    def test_list_dir_returns_relative_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "subdir").mkdir()
            (root / "demo.txt").write_text("demo", encoding="utf-8")
            previous = os.environ.get("CREATIVE_CLAW_WORKSPACE")
            os.environ["CREATIVE_CLAW_WORKSPACE"] = str(root)
            try:
                result = list_dir(".")

                self.assertIn("[D] subdir", result)
                self.assertIn("[F] demo.txt", result)
            finally:
                if previous is None:
                    os.environ.pop("CREATIVE_CLAW_WORKSPACE", None)
                else:
                    os.environ["CREATIVE_CLAW_WORKSPACE"] = previous

    def test_outside_workspace_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            previous = os.environ.get("CREATIVE_CLAW_WORKSPACE")
            os.environ["CREATIVE_CLAW_WORKSPACE"] = tmpdir
            try:
                result = read_file("../outside.txt")

                self.assertIn("Error reading file", result)
            finally:
                if previous is None:
                    os.environ.pop("CREATIVE_CLAW_WORKSPACE", None)
                else:
                    os.environ["CREATIVE_CLAW_WORKSPACE"] = previous

    def test_exec_command_blocks_dangerous_pattern(self) -> None:
        result = exec_command("rm -rf /tmp/demo")
        self.assertIn("Command blocked by safety guard", result)

    def test_web_fetch_rejects_invalid_scheme(self) -> None:
        result = web_fetch("file:///tmp/demo.txt")
        self.assertIn("Only http/https URLs are supported.", result)

    def test_toolbox_can_use_explicit_workspace_without_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            toolbox = BuiltinToolbox(root)

            self.assertIn("Successfully wrote", toolbox.write_file("nested/demo.txt", "abc"))
            self.assertEqual(toolbox.read_file("nested/demo.txt"), "abc")
            self.assertIn("[F] nested/demo.txt", toolbox.list_dir("nested"))


if __name__ == "__main__":
    unittest.main()
