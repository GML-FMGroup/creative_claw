import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
