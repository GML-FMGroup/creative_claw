import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.production.models import ProductionOwnerRef
from src.production.session_store import ProductionSessionStore
from src.production.short_video.manager import ShortVideoProductionManager
from src.production.short_video.models import (
    AssetManifestEntry,
    AudioManifestEntry,
    RenderReport,
    RenderValidationReport,
    ShortVideoRenderSettings,
)
from src.production.short_video.tool import run_short_video_production
from src.runtime.workspace import resolve_workspace_path, workspace_relative_path, workspace_root


_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _adk_state() -> dict:
    return {
        "sid": "session_short_video_test",
        "turn_index": 1,
        "step": 0,
        "channel": "cli",
        "chat_id": "terminal",
        "sender_id": "cli-user",
        "uploaded": [],
        "generated": [],
        "files_history": [],
        "final_file_paths": [],
    }


class _FakePlaceholderFactory:
    def create(
        self,
        *,
        session_root: Path,
        render_settings: ShortVideoRenderSettings,
        duration_seconds: float,
    ):
        assets_dir = session_root / "assets"
        audio_dir = session_root / "audio"
        assets_dir.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(parents=True, exist_ok=True)
        video_path = assets_dir / "placeholder_clip.mp4"
        image_path = assets_dir / "placeholder_keyframe.png"
        audio_path = audio_dir / "placeholder_silence.m4a"
        video_path.write_bytes(b"fake-video")
        image_path.write_bytes(b"fake-image")
        audio_path.write_bytes(b"fake-audio")
        return (
            [
                AssetManifestEntry(
                    asset_id="asset_image_1",
                    kind="image",
                    path=workspace_relative_path(image_path),
                    source="placeholder",
                ),
                AssetManifestEntry(
                    asset_id="asset_video_1",
                    kind="video",
                    path=workspace_relative_path(video_path),
                    source="placeholder",
                    duration_seconds=duration_seconds,
                    width=render_settings.width,
                    height=render_settings.height,
                ),
            ],
            [
                AudioManifestEntry(
                    audio_id="audio_1",
                    kind="silent",
                    path=workspace_relative_path(audio_path),
                    source="placeholder",
                    duration_seconds=duration_seconds,
                )
            ],
        )


class _FakeRenderer:
    def render(self, *, timeline, asset_manifest, audio_manifest, output_path: Path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-final-video")
        return RenderReport(
            output_path=workspace_relative_path(output_path),
            duration_seconds=timeline.duration_seconds,
            width=timeline.render_settings.width,
            height=timeline.render_settings.height,
            video_codec="h264",
            audio_codec="aac",
            command_summary="fake render",
        )


class _FakeValidator:
    def validate(self, path: str):
        return RenderValidationReport(
            status="valid",
            path=path,
            duration_seconds=4.0,
            width=1280,
            height=720,
            has_video=True,
            has_audio=True,
        )


class ShortVideoProductionTests(unittest.TestCase):
    def test_manager_start_p0a_saves_state_and_projects_final_artifact(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            placeholder_factory=_FakePlaceholderFactory(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )

        result = manager.start(
            user_prompt="make a placeholder video",
            input_files=[],
            placeholder_assets=True,
            render_settings={"aspect_ratio": "16:9", "duration_seconds": 4},
            adk_state=state,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.stage, "completed")
        self.assertEqual(len(result.artifacts), 1)
        self.assertEqual(state["final_file_paths"], [result.artifacts[0].path])
        self.assertEqual(state["generated"][0]["source"], "short_video")
        state_path = resolve_workspace_path(result.state_ref or "")
        self.assertTrue(state_path.exists())
        self.assertTrue((state_path.parent / "events.jsonl").exists())
        self.assertTrue((state_path.parent / "brief.md").exists())
        self.assertTrue((state_path.parent / "asset_plan.json").exists())
        self.assertTrue((state_path.parent / "timeline.json").exists())

    def test_manager_status_uses_owner_check(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            placeholder_factory=_FakePlaceholderFactory(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = manager.start(
            user_prompt="status test",
            input_files=[],
            placeholder_assets=True,
            render_settings={},
            adk_state=state,
        )

        status = manager.status(
            production_session_id=started.production_session_id,
            adk_state=state,
        )

        self.assertEqual(status.status, "completed")
        self.assertEqual(status.production_session_id, started.production_session_id)

        wrong_state = dict(state)
        wrong_state["sid"] = "other_session"
        wrong_status = manager.status(
            production_session_id=started.production_session_id,
            adk_state=wrong_state,
        )

        self.assertEqual(wrong_status.status, "failed")
        self.assertEqual(wrong_status.error.code, "production_session_not_found_or_not_owned")

    def test_tool_uses_uploaded_files_when_input_files_are_omitted(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            upload_path = Path(tmpdir) / "product.png"
            upload_path.write_bytes(b"fake-upload")
            state = _adk_state()
            state["uploaded"] = [
                {
                    "name": "product.png",
                    "path": workspace_relative_path(upload_path),
                    "description": "product reference",
                    "source": "channel",
                }
            ]
            tool_context = SimpleNamespace(state=state)

            result = asyncio.run(
                run_short_video_production(
                    action="start",
                    user_prompt="make a placeholder video from this product",
                    placeholder_assets=False,
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result["status"], "needs_user_input")
        self.assertIn("placeholder_assets=true", result["message"])

    def test_store_owner_check_rejects_other_session(self) -> None:
        store = ProductionSessionStore()
        session = store.create_session(
            capability="short_video",
            adk_session_id="owner_check_session",
            turn_index=1,
            owner_ref=ProductionOwnerRef(channel="cli", chat_id="terminal", sender_id="user"),
        )

        with self.assertRaisesRegex(Exception, "production_session_not_found_or_not_owned"):
            store.load_state(
                production_session_id=session.production_session_id,
                adk_session_id="other_session",
                owner_ref=ProductionOwnerRef(channel="cli", chat_id="terminal", sender_id="user"),
                state_type=object,  # type: ignore[arg-type]
            )


class ShortVideoProductionAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_requires_context(self) -> None:
        result = await run_short_video_production(action="start", placeholder_assets=True)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "missing_tool_context")


@unittest.skipUnless(_HAS_FFMPEG, "ffmpeg/ffprobe not available")
class ShortVideoProductionFfmpegTests(unittest.TestCase):
    def test_real_p0a_placeholder_render_outputs_playable_mp4(self) -> None:
        state = _adk_state()
        state["sid"] = "session_short_video_ffmpeg_test"
        manager = ShortVideoProductionManager()

        result = manager.start(
            user_prompt="make a real placeholder video",
            input_files=[],
            placeholder_assets=True,
            render_settings={"aspect_ratio": "1:1", "duration_seconds": 1},
            adk_state=state,
        )

        self.assertEqual(result.status, "completed")
        final_path = resolve_workspace_path(result.artifacts[0].path)
        self.assertTrue(final_path.exists())
        self.assertGreater(final_path.stat().st_size, 0)
