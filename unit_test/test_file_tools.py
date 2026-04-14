import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from conf.api import API_CONFIG
from src.tools.builtin_tools import (
    BuiltinToolbox,
    exec_command,
    web_fetch,
)


_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _run_media_command(args: list[str]) -> None:
    subprocess.run(
        args,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _create_test_video(path: Path, *, color: str, tone: int) -> None:
    _run_media_command(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=320x240:d=1.0",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={tone}:duration=1.0",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ]
    )


def _create_test_audio(path: Path, *, tone: int) -> None:
    _run_media_command(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={tone}:duration=1.0",
            "-c:a",
            "pcm_s16le",
            str(path),
        ]
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
        with patch.object(API_CONFIG, "BRAVE_API_KEY", ""):
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

    def test_image_info_resize_and_convert_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            toolbox = BuiltinToolbox(root)
            source = root / "sample.png"
            Image.new("RGBA", (120, 80), color=(255, 0, 0, 200)).save(source)

            info = json.loads(toolbox.image_info("sample.png"))
            resized_path = toolbox.image_resize("sample.png", width=60)
            converted_path = toolbox.image_convert("sample.png", "jpg", quality=80)

            self.assertEqual(info["format"], "PNG")
            self.assertEqual(info["width"], 120)
            self.assertEqual(info["height"], 80)
            self.assertEqual(info["mode"], "RGBA")
            self.assertEqual(resized_path, "sample_resize_60x40.png")
            self.assertEqual(converted_path, "sample_convert.jpg")
            self.assertTrue((root / resized_path).exists())
            self.assertTrue((root / converted_path).exists())

    @unittest.skipUnless(_HAS_FFMPEG, "ffmpeg/ffprobe not available")
    def test_video_tools_info_extract_trim_concat_and_convert(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            toolbox = BuiltinToolbox(root)
            _create_test_video(root / "clip_a.mp4", color="red", tone=440)
            _create_test_video(root / "clip_b.mp4", color="blue", tone=660)

            info = json.loads(toolbox.video_info("clip_a.mp4"))
            frame_path = toolbox.video_extract_frame("clip_a.mp4", "0.2")
            trimmed_path = toolbox.video_trim("clip_a.mp4", "0", duration="0.5")
            concat_path = toolbox.video_concat(["clip_a.mp4", "clip_b.mp4"])
            converted_path = toolbox.video_convert("clip_a.mp4", "mov")

            self.assertEqual(info["width"], 320)
            self.assertEqual(info["height"], 240)
            self.assertEqual(frame_path, "clip_a_frame_0.2.png")
            self.assertEqual(trimmed_path, "clip_a_trim.mp4")
            self.assertEqual(concat_path, "clip_a_concat.mp4")
            self.assertEqual(converted_path, "clip_a_convert.mov")
            self.assertTrue((root / frame_path).exists())
            self.assertTrue((root / trimmed_path).exists())
            self.assertTrue((root / concat_path).exists())
            self.assertTrue((root / converted_path).exists())

    @unittest.skipUnless(_HAS_FFMPEG, "ffmpeg/ffprobe not available")
    def test_audio_tools_info_trim_concat_and_convert(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            toolbox = BuiltinToolbox(root)
            _create_test_audio(root / "tone_a.wav", tone=440)
            _create_test_audio(root / "tone_b.wav", tone=660)

            info = json.loads(toolbox.audio_info("tone_a.wav"))
            trimmed_path = toolbox.audio_trim("tone_a.wav", "0", duration="0.5")
            concat_path = toolbox.audio_concat(["tone_a.wav", "tone_b.wav"])
            converted_path = toolbox.audio_convert("tone_a.wav", "mp3", bitrate="128k")

            self.assertEqual(info["sample_rate"], 44100)
            self.assertEqual(info["channels"], 1)
            self.assertEqual(trimmed_path, "tone_a_trim.wav")
            self.assertEqual(concat_path, "tone_a_concat.wav")
            self.assertEqual(converted_path, "tone_a_convert.mp3")
            self.assertTrue((root / trimmed_path).exists())
            self.assertTrue((root / concat_path).exists())
            self.assertTrue((root / converted_path).exists())

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
