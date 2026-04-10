import re
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from src.tools.builtin_tools import (
    BuiltinToolbox,
    exec_command,
    web_fetch,
)


class BuiltinToolTests(unittest.TestCase):
    def test_read_write_and_edit_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            toolbox = BuiltinToolbox(root)
            write_result = toolbox.write_file("demo.txt", "hello world")
            read_result = toolbox.read_file("demo.txt")
            edit_result = toolbox.edit_file("demo.txt", "world", "creative_claw")

            self.assertIn("Successfully wrote", write_result)
            self.assertEqual(read_result, "hello world")
            self.assertIn("Successfully edited", edit_result)
            self.assertEqual(toolbox.read_file("demo.txt"), "hello creative_claw")

    def test_list_dir_returns_relative_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "subdir").mkdir()
            (root / "demo.txt").write_text("demo", encoding="utf-8")
            toolbox = BuiltinToolbox(root)
            result = toolbox.list_dir(".")

            self.assertIn("[D] subdir", result)
            self.assertIn("[F] demo.txt", result)

    def test_glob_finds_matching_files_and_skips_ignored_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src" / "nested").mkdir(parents=True)
            (root / "node_modules").mkdir()
            (root / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
            (root / "src" / "nested" / "worker.py").write_text("print('worker')\n", encoding="utf-8")
            (root / "node_modules" / "skip.py").write_text("print('skip')\n", encoding="utf-8")
            toolbox = BuiltinToolbox(root)

            result = toolbox.glob("*.py", path=".")

            self.assertIn("src/app.py", result)
            self.assertIn("src/nested/worker.py", result)
            self.assertNotIn("node_modules/skip.py", result)

    def test_grep_supports_glob_filter_and_content_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text(
                "def hello():\n    return 'hello creative claw'\n",
                encoding="utf-8",
            )
            (root / "notes.txt").write_text("hello from notes\n", encoding="utf-8")
            toolbox = BuiltinToolbox(root)

            result = toolbox.grep(
                "hello",
                path=".",
                glob_pattern="*.py",
                output_mode="content",
                context_after=1,
            )

            self.assertIn("src/app.py:1", result)
            self.assertIn("return 'hello creative claw'", result)
            self.assertNotIn("notes.txt", result)

    def test_outside_workspace_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            toolbox = BuiltinToolbox(tmpdir)
            result = toolbox.read_file("../outside.txt")
            self.assertIn("Error reading file", result)

    def test_exec_command_blocks_dangerous_pattern(self) -> None:
        result = exec_command("rm -rf /tmp/demo")
        self.assertIn("Command blocked by safety guard", result)

    def test_web_fetch_rejects_invalid_scheme(self) -> None:
        result = web_fetch("file:///tmp/demo.txt")
        self.assertIn("Only http/https URLs are supported.", result)

    def test_web_search_reports_missing_api_key_without_crashing(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = BuiltinToolbox(Path.cwd()).web_search("creative claw")

        self.assertEqual(result, "Error: BRAVE_API_KEY not configured")

    def test_toolbox_can_use_explicit_workspace_without_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            toolbox = BuiltinToolbox(root)

            self.assertIn("Successfully wrote", toolbox.write_file("nested/demo.txt", "abc"))
            self.assertEqual(toolbox.read_file("nested/demo.txt"), "abc")
            self.assertIn("[F] nested/demo.txt", toolbox.list_dir("nested"))

    def test_image_tools_save_outputs_with_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            toolbox = BuiltinToolbox(root)
            source = root / "sample.png"
            Image.new("RGB", (100, 80), color="red").save(source)

            cropped_path = toolbox.image_crop("sample.png", 10, 10, 60, 50)
            rotated_path = toolbox.image_rotate("sample.png", 90)
            flipped_path = toolbox.image_flip("sample.png", "horizontal")

            self.assertEqual(cropped_path, "sample_crop.png")
            self.assertEqual(rotated_path, "sample_rotate_90.png")
            self.assertEqual(flipped_path, "sample_flip_horizontal.png")
            self.assertTrue((root / cropped_path).exists())
            self.assertTrue((root / rotated_path).exists())
            self.assertTrue((root / flipped_path).exists())

    def test_background_exec_and_process_session_follow_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            toolbox = BuiltinToolbox(root)
            scope_key = "unit-test-process-session"
            command = (
                f"{shlex.quote(sys.executable)} -c "
                "\"import time; time.sleep(0.3); print('done', flush=True)\""
            )

            start_result = toolbox.exec_command(
                command,
                working_dir=".",
                background=True,
                yield_ms=0,
                scope_key=scope_key,
            )

            self.assertIn("Command still running", start_result)
            match = re.search(r"session ([^,\s]+)", start_result)
            self.assertIsNotNone(match)
            session_id = match.group(1)

            listed = toolbox.process_session(action="list", scope_key=scope_key)
            self.assertIn(session_id, listed)

            poll_result = toolbox.process_session(
                action="poll",
                session_id=session_id,
                timeout_ms=1500,
                scope_key=scope_key,
            )
            self.assertIn("done", poll_result)
            if "Status: exited" not in poll_result:
                poll_result = toolbox.process_session(
                    action="poll",
                    session_id=session_id,
                    timeout_ms=500,
                    scope_key=scope_key,
                )
            self.assertIn("Status: exited", poll_result)

            log_result = toolbox.process_session(
                action="log",
                session_id=session_id,
                scope_key=scope_key,
            )
            self.assertIn("done", log_result)

            remove_result = toolbox.process_session(
                action="remove",
                session_id=session_id,
                scope_key=scope_key,
            )
            self.assertEqual(remove_result, f"Removed session {session_id}.")


if __name__ == "__main__":
    unittest.main()
