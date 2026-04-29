import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.production.models import ProductionOwnerRef
from src.production.session_store import ProductionSessionStore
from src.production.short_video.manager import ShortVideoProductionManager
from src.production.short_video.models import (
    AssetManifestEntry,
    AudioManifestEntry,
    RenderReport,
    RenderValidationReport,
    ShortVideoAssetPlan,
    ShortVideoRenderSettings,
    ShortVideoShotPlan,
)
from src.production.short_video.providers import (
    RoutedShortVideoProviderRuntime,
    SeedanceNativeAudioProviderRuntime,
    ShortVideoProviderError,
    VeoTtsProviderRuntime,
)
from src.production.short_video.tool import run_short_video_production
from src.runtime.step_events import configure_step_event_publisher
from src.runtime.tool_context import route_context
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


class _FakeProviderRuntime:
    async def generate_video_clip(
        self,
        *,
        session_root: Path,
        asset_plan,
        render_settings: ShortVideoRenderSettings,
        reference_assets,
        owner_ref,
    ) -> AssetManifestEntry:
        video_path = session_root / "assets" / f"{asset_plan.plan_id}_veo_clip.mp4"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"fake-veo-video")
        return AssetManifestEntry(
            asset_id=f"{asset_plan.plan_id}_video",
            kind="video",
            path=workspace_relative_path(video_path),
            source="expert",
            provider="veo",
            prompt_ref=asset_plan.plan_id,
            duration_seconds=asset_plan.duration_seconds,
            width=render_settings.width,
            height=render_settings.height,
            derived_from=asset_plan.reference_asset_ids,
        )

    async def synthesize_voiceover(
        self,
        *,
        session_root: Path,
        asset_plan,
        render_settings: ShortVideoRenderSettings,
        owner_ref,
    ) -> AudioManifestEntry:
        audio_path = session_root / "audio" / f"{asset_plan.plan_id}_voiceover.mp3"
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(b"fake-tts-audio")
        return AudioManifestEntry(
            audio_id=f"{asset_plan.plan_id}_voiceover",
            kind="voiceover",
            path=workspace_relative_path(audio_path),
            source="expert",
            provider="mock_tts",
            duration_seconds=asset_plan.duration_seconds,
        )


def _approve_storyboard(
    manager: ShortVideoProductionManager,
    *,
    production_session_id: str,
    adk_state: dict,
    selected_ratio: str | None = None,
):
    response = {"decision": "approve"}
    if selected_ratio is not None:
        response["selected_ratio"] = selected_ratio
    return asyncio.run(manager.resume(
        production_session_id=production_session_id,
        user_response=response,
        adk_state=adk_state,
    ))


def _approve_asset_plan(
    manager: ShortVideoProductionManager,
    *,
    production_session_id: str,
    adk_state: dict,
    selected_ratio: str | None = None,
):
    result = _approve_asset_plan_to_shot_review(
        manager,
        production_session_id=production_session_id,
        adk_state=adk_state,
        selected_ratio=selected_ratio,
    )
    while result.stage == "shot_review":
        result = _approve_shot_review(
            manager,
            production_session_id=production_session_id,
            adk_state=adk_state,
        )
    return result


def _approve_asset_plan_to_shot_review(
    manager: ShortVideoProductionManager,
    *,
    production_session_id: str,
    adk_state: dict,
    selected_ratio: str | None = None,
):
    response = {"decision": "approve"}
    if selected_ratio is not None:
        response["selected_ratio"] = selected_ratio
    return asyncio.run(manager.resume(
        production_session_id=production_session_id,
        user_response=response,
        adk_state=adk_state,
    ))


def _approve_shot_review(
    manager: ShortVideoProductionManager,
    *,
    production_session_id: str,
    adk_state: dict,
):
    return asyncio.run(manager.resume(
        production_session_id=production_session_id,
        user_response={"decision": "approve"},
        adk_state=adk_state,
    ))


async def _approve_storyboard_async(
    manager: ShortVideoProductionManager,
    *,
    production_session_id: str,
    adk_state: dict,
    selected_ratio: str | None = None,
):
    response = {"decision": "approve"}
    if selected_ratio is not None:
        response["selected_ratio"] = selected_ratio
    return await manager.resume(
        production_session_id=production_session_id,
        user_response=response,
        adk_state=adk_state,
    )


async def _approve_asset_plan_async(
    manager: ShortVideoProductionManager,
    *,
    production_session_id: str,
    adk_state: dict,
    selected_ratio: str | None = None,
):
    result = await _approve_asset_plan_to_shot_review_async(
        manager,
        production_session_id=production_session_id,
        adk_state=adk_state,
        selected_ratio=selected_ratio,
    )
    while result.stage == "shot_review":
        result = await _approve_shot_review_async(
            manager,
            production_session_id=production_session_id,
            adk_state=adk_state,
        )
    return result


async def _approve_asset_plan_to_shot_review_async(
    manager: ShortVideoProductionManager,
    *,
    production_session_id: str,
    adk_state: dict,
    selected_ratio: str | None = None,
):
    response = {"decision": "approve"}
    if selected_ratio is not None:
        response["selected_ratio"] = selected_ratio
    return await manager.resume(
        production_session_id=production_session_id,
        user_response=response,
        adk_state=adk_state,
    )


async def _approve_shot_review_async(
    manager: ShortVideoProductionManager,
    *,
    production_session_id: str,
    adk_state: dict,
):
    return await manager.resume(
        production_session_id=production_session_id,
        user_response={"decision": "approve"},
        adk_state=adk_state,
    )


class ShortVideoProductionTests(unittest.TestCase):
    def test_manager_start_p0a_saves_state_and_projects_final_artifact(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            placeholder_factory=_FakePlaceholderFactory(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )

        result = asyncio.run(manager.start(
            user_prompt="make a placeholder video",
            input_files=[],
            placeholder_assets=True,
            render_settings={"aspect_ratio": "16:9", "duration_seconds": 4},
            adk_state=state,
        ))

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
        self.assertTrue((state_path.parent / "quality_report.json").exists())
        self.assertTrue((state_path.parent / "quality_report.md").exists())

    def test_manager_status_uses_owner_check(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            placeholder_factory=_FakePlaceholderFactory(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="status test",
            input_files=[],
            placeholder_assets=True,
            render_settings={},
            adk_state=state,
        ))

        status = asyncio.run(manager.status(
            production_session_id=started.production_session_id,
            adk_state=state,
        ))

        self.assertEqual(status.status, "completed")
        self.assertEqual(status.production_session_id, started.production_session_id)

        wrong_state = dict(state)
        wrong_state["sid"] = "other_session"
        wrong_status = asyncio.run(manager.status(
            production_session_id=started.production_session_id,
            adk_state=wrong_state,
        ))

        self.assertEqual(wrong_status.status, "failed")
        self.assertEqual(wrong_status.error.code, "production_session_not_found_or_not_owned")

    def test_manager_start_returns_storyboard_review(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager()

        result = asyncio.run(manager.start(
            user_prompt="make a product ad for a desk lamp",
            input_files=[],
            placeholder_assets=False,
            render_settings={},
            adk_state=state,
        ))

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "storyboard_review")
        self.assertEqual(result.review_payload.review_type, "storyboard_review")
        self.assertEqual(state["active_production_session_id"], result.production_session_id)
        self.assertEqual(state["active_production_status"], "needs_user_review")
        state_path = resolve_workspace_path(result.state_ref or "")
        storyboard_payload = json.loads((state_path.parent / "storyboard.json").read_text(encoding="utf-8"))
        self.assertEqual(storyboard_payload["storyboard"]["video_type"], "product_ad")
        self.assertEqual(len(storyboard_payload["storyboard"]["shots"]), 3)
        self.assertIn("active_review", storyboard_payload)

    def test_manager_storyboard_approval_returns_asset_plan_review(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager()

        started = asyncio.run(manager.start(
            user_prompt="make a product ad for a desk lamp",
            input_files=[],
            placeholder_assets=False,
            render_settings={},
            adk_state=state,
        ))
        result = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "asset_plan_review")
        self.assertEqual(result.review_payload.review_type, "asset_plan_review")
        state_path = resolve_workspace_path(result.state_ref or "")
        asset_plan_payload = json.loads((state_path.parent / "asset_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(asset_plan_payload["asset_plan"]["planned_video_provider"], "seedance")
        self.assertEqual(asset_plan_payload["asset_plan"]["planned_video_model_name"], "doubao-seedance-2-0-260128")
        self.assertEqual(asset_plan_payload["asset_plan"]["planned_video_resolution"], "720p")
        self.assertTrue(asset_plan_payload["asset_plan"]["planned_generate_audio"])
        self.assertIsNone(asset_plan_payload["asset_plan"]["selected_ratio"])
        self.assertGreaterEqual(len(asset_plan_payload["shot_asset_plans"]), 1)
        shot_plan_item = next(
            item for item in result.review_payload.model_dump(mode="json")["items"]
            if item["kind"] == "shot_asset_plans"
        )
        self.assertGreaterEqual(shot_plan_item["count"], 1)
        self.assertIn("active_review", asset_plan_payload)

    def test_manager_start_classifies_cartoon_and_social_video_types(self) -> None:
        manager = ShortVideoProductionManager()
        cases = [
            (
                "帮我做一个 20 秒卡通短剧，主题是程序员被 AI 助手拯救",
                "cartoon_short_drama",
                "cartoon short-drama",
                {"aspect_ratio": "9:16"},
            ),
            (
                "做一个适合小红书发布的社交媒体短片，开头要有强钩子",
                "social_media_short",
                "social-media short",
                {"aspect_ratio": "9:16"},
            ),
            (
                "帮我做一支 15 秒短视频，先给计划",
                "social_media_short",
                "social-media short",
                {"aspect_ratio": "9:16", "project_type": "social_media_short"},
            ),
        ]

        for prompt, expected_video_type, prompt_marker, render_settings in cases:
            with self.subTest(expected_video_type=expected_video_type):
                state = _adk_state()
                state["sid"] = f"session_{expected_video_type}"

                result = asyncio.run(manager.start(
                    user_prompt=prompt,
                    input_files=[],
                    placeholder_assets=False,
                    render_settings=render_settings,
                    adk_state=state,
                ))

                self.assertEqual(result.status, "needs_user_review")
                state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
                storyboard = state_payload["storyboard"]
                self.assertEqual(storyboard["video_type"], expected_video_type)
                self.assertIn(prompt_marker, storyboard["narrative_summary"])
                review_items = result.review_payload.model_dump(mode="json")["items"]
                video_type_item = next(item for item in review_items if item["kind"] == "video_type")
                self.assertEqual(video_type_item["video_type"], expected_video_type)

    def test_manager_start_accepts_seedance_fast_model_settings(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager()

        result = asyncio.run(manager.start(
            user_prompt="make a faster social media short",
            input_files=[],
            placeholder_assets=False,
            render_settings={
                "aspect_ratio": "9:16",
                "model_name": "doubao-seedance-2-0-fast-260128",
                "resolution": "1080p",
            },
            adk_state=state,
        ))
        result = _approve_storyboard(
            manager,
            production_session_id=result.production_session_id,
            adk_state=state,
        )

        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        asset_plan = state_payload["asset_plan"]
        self.assertEqual(asset_plan["planned_video_model_name"], "doubao-seedance-2-0-fast-260128")
        self.assertEqual(asset_plan["planned_video_resolution"], "720p")
        providers_item = next(item for item in result.review_payload.model_dump(mode="json")["items"] if item["kind"] == "providers")
        self.assertEqual(providers_item["planned_video_model_name"], "doubao-seedance-2-0-fast-260128")
        self.assertEqual(providers_item["planned_video_resolution"], "720p")

    def test_manager_start_accepts_veo_tts_runtime_settings(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager()

        result = asyncio.run(manager.start(
            user_prompt="make a Veo product ad with separate TTS",
            input_files=[],
            placeholder_assets=False,
            render_settings={
                "provider": "veo_tts",
                "aspect_ratio": "1:1",
                "duration_seconds": 8,
            },
            adk_state=state,
        ))
        result = _approve_storyboard(
            manager,
            production_session_id=result.production_session_id,
            adk_state=state,
        )

        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        asset_plan = state_payload["asset_plan"]
        self.assertEqual(asset_plan["planned_video_provider"], "veo")
        self.assertEqual(asset_plan["planned_video_model_name"], "veo-3.1-generate-preview")
        self.assertEqual(asset_plan["planned_video_resolution"], "720p")
        self.assertFalse(asset_plan["planned_generate_audio"])
        self.assertTrue(asset_plan["planned_tts"])
        self.assertEqual(asset_plan["planned_tts_provider"], "bytedance_tts")
        self.assertEqual(asset_plan["ratio_options"], ["9:16", "16:9"])
        self.assertIsNone(asset_plan["selected_ratio"])
        providers_item = next(item for item in result.review_payload.model_dump(mode="json")["items"] if item["kind"] == "providers")
        self.assertEqual(providers_item["planned_video_provider"], "veo")
        self.assertEqual(providers_item["provider_label"], "Veo + ByteDance TTS")

    def test_manager_veo_segment_duration_quantizes_to_supported_value(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager()

        result = asyncio.run(manager.start(
            user_prompt="make a Veo product ad with a seven second target",
            input_files=[],
            placeholder_assets=False,
            render_settings={
                "provider": "veo_tts",
                "aspect_ratio": "9:16",
                "duration_seconds": 7,
            },
            adk_state=state,
        ))
        result = _approve_storyboard(
            manager,
            production_session_id=result.production_session_id,
            adk_state=state,
        )

        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(state_payload["shot_asset_plans"][0]["duration_seconds"], 8.0)

    def test_manager_veo_reference_assets_force_eight_seconds_and_warn_on_truncation(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            provider_runtime=_FakeProviderRuntime(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            input_files = []
            for index in range(4):
                reference_path = Path(tmpdir) / f"product_{index}.png"
                reference_path.write_bytes(b"product-reference")
                input_files.append(
                    {
                        "name": reference_path.name,
                        "path": workspace_relative_path(reference_path),
                    }
                )

            started = asyncio.run(manager.start(
                user_prompt="make a Veo product ad from these product references",
                input_files=input_files,
                placeholder_assets=False,
                render_settings={
                    "provider": "veo_tts",
                    "aspect_ratio": "9:16",
                    "duration_seconds": 6,
                },
                adk_state=state,
            ))
            asset_review = _approve_storyboard(
                manager,
                production_session_id=started.production_session_id,
                adk_state=state,
            )
            shot_review = _approve_asset_plan_to_shot_review(
                manager,
                production_session_id=started.production_session_id,
                adk_state=state,
            )

        asset_review_payload = json.loads(resolve_workspace_path(asset_review.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(asset_review_payload["shot_asset_plans"][0]["duration_seconds"], 8.0)
        shot_payload = json.loads(resolve_workspace_path(shot_review.state_ref or "").read_text(encoding="utf-8"))
        warning_events = [
            event
            for event in shot_payload["production_events"]
            if event["event_type"] == "reference_asset_limit_warning"
        ]
        self.assertEqual(len(warning_events), 1)
        self.assertEqual(warning_events[0]["metadata"]["limit"], 3)
        self.assertEqual(len(warning_events[0]["metadata"]["omitted_reference_asset_ids"]), 1)
        omitted_id = warning_events[0]["metadata"]["omitted_reference_asset_ids"][0]
        omitted_reference = next(
            item for item in shot_payload["reference_assets"] if item["reference_asset_id"] == omitted_id
        )
        self.assertIn("warnings", omitted_reference["metadata"])

    def test_manager_start_rejects_unsupported_short_video_provider(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager()

        result = asyncio.run(manager.start(
            user_prompt="make a short video with Kling",
            input_files=[],
            placeholder_assets=False,
            render_settings={"provider": "kling", "aspect_ratio": "9:16"},
            adk_state=state,
        ))

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.stage, "failed")
        self.assertEqual(result.error.code, "short_video_start_failed")
        self.assertIn("Unsupported short-video provider", result.error.message)

    def test_cartoon_dialogue_plan_preserves_character_lines_for_native_audio(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager()
        prompt = "\n".join(
            [
                "给我做一个短视频。是关于两只猫咪的对话",
                "猫A: 你妈妈一个月赚多少钱？诚实说。",
                "猫B：嗯嗯。。两万五",
                "不用显示字幕。但是需要有语音。",
                "语音风格软萌萌可爱",
            ]
        )

        result = asyncio.run(manager.start(
            user_prompt=prompt,
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "9:16"},
            adk_state=state,
        ))

        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        storyboard = state_payload["storyboard"]
        storyboard_dialogue = [
            line
            for shot in storyboard["shots"]
            for line in shot["dialogue_lines"]
        ]
        self.assertIn('猫A says "你妈妈一个月赚多少钱？诚实说。"', storyboard_dialogue)
        self.assertIn('猫B says "嗯嗯。。两万五"', storyboard_dialogue)
        self.assertIn("Do not render subtitles or on-screen captions.", storyboard["global_constraints"])

        result = _approve_storyboard(
            manager,
            production_session_id=result.production_session_id,
            adk_state=state,
        )
        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        visual_prompt = state_payload["asset_plan"]["shot_plan"]["visual_prompt"]
        self.assertIn('猫A says "你妈妈一个月赚多少钱？诚实说。"', visual_prompt)
        self.assertIn('猫B says "嗯嗯。。两万五"', visual_prompt)
        self.assertIn("with no narrator reading the task description", visual_prompt)
        self.assertIn("Do not render subtitles", visual_prompt)

    def test_manager_view_returns_storyboard_without_mutating_adk_state(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager()
        started = asyncio.run(manager.start(
            user_prompt="make a product ad for a desk lamp",
            input_files=[],
            placeholder_assets=False,
            render_settings={},
            adk_state=state,
        ))
        state_before_view = json.dumps(state, sort_keys=True)

        result = asyncio.run(manager.view(
            production_session_id=started.production_session_id,
            view_type="storyboard",
            adk_state=state,
        ))

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "storyboard_review")
        self.assertEqual(result.view["view_type"], "storyboard")
        self.assertEqual(result.view["storyboard"]["video_type"], "product_ad")
        self.assertEqual(result.view["active_review"]["review_type"], "storyboard_review")
        self.assertEqual(json.dumps(state, sort_keys=True), state_before_view)

    def test_manager_view_events_uses_owner_check(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager()
        started = asyncio.run(manager.start(
            user_prompt="make a product ad",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "9:16"},
            adk_state=state,
        ))

        events_view = asyncio.run(manager.view(
            production_session_id=started.production_session_id,
            view_type="events",
            adk_state=state,
        ))
        self.assertEqual(events_view.status, "needs_user_review")
        self.assertEqual(events_view.view["view_type"], "events")
        self.assertGreaterEqual(len(events_view.view["events"]), 1)

        wrong_state = dict(state)
        wrong_state["sid"] = "other_session"
        wrong_view = asyncio.run(manager.view(
            production_session_id=started.production_session_id,
            view_type="events",
            adk_state=wrong_state,
        ))
        self.assertEqual(wrong_view.status, "failed")
        self.assertEqual(wrong_view.error.code, "production_session_not_found_or_not_owned")

    def test_manager_view_rejects_invalid_view_type(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager()
        started = asyncio.run(manager.start(
            user_prompt="make a product ad",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "9:16"},
            adk_state=state,
        ))

        result = asyncio.run(manager.view(
            production_session_id=started.production_session_id,
            view_type="unknown",
            adk_state=state,
        ))

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.stage, "invalid_view_type")
        self.assertEqual(result.error.code, "invalid_view_type")

    def test_manager_resume_requires_ratio_before_provider_generation(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(provider_runtime=_FakeProviderRuntime())
        started = asyncio.run(manager.start(
            user_prompt="make a product ad",
            input_files=[],
            placeholder_assets=False,
            render_settings={},
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )

        result = asyncio.run(manager.resume(
            production_session_id=asset_review.production_session_id,
            user_response={"decision": "approve"},
            adk_state=state,
        ))

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "asset_plan_review")
        self.assertIn("aspect ratio", result.message)
        self.assertEqual(state["active_production_status"], "needs_user_review")

    def test_manager_resume_approve_generates_p0b_with_fake_providers(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            provider_runtime=_FakeProviderRuntime(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="make a clean product ad for a smart mug",
            input_files=[],
            placeholder_assets=False,
            render_settings={},
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
            selected_ratio="9:16",
        )

        shot_review = asyncio.run(manager.resume(
            production_session_id=asset_review.production_session_id,
            user_response={"decision": "approve"},
            adk_state=state,
        ))

        self.assertEqual(shot_review.status, "needs_user_review")
        self.assertEqual(shot_review.stage, "shot_review")
        self.assertEqual(shot_review.review_payload.review_type, "shot_review")
        self.assertEqual(len(shot_review.artifacts), 1)
        shot_payload = json.loads(resolve_workspace_path(shot_review.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(shot_payload["asset_plan"]["selected_ratio"], "9:16")
        self.assertEqual(shot_payload["shot_artifacts"][0]["status"], "generated")
        self.assertEqual(shot_payload["asset_manifest"][0]["provider"], "veo")
        self.assertEqual(shot_payload["audio_manifest"][0]["provider"], "mock_tts")

        result = _approve_shot_review(
            manager,
            production_session_id=asset_review.production_session_id,
            adk_state=state,
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.stage, "completed")
        self.assertEqual(state["final_file_paths"], [result.artifacts[0].path])
        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(state_payload["shot_artifacts"][0]["status"], "approved")
        self.assertIsNotNone(state_payload["quality_report"])

    def test_manager_completed_video_writes_quality_report_and_quality_view(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            provider_runtime=_FakeProviderRuntime(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt=(
                "给我做一个短视频，是关于两只猫咪的对话。不用显示字幕，"
                "但是需要有语音，语音风格软萌萌可爱。"
            ),
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "16:9", "duration_seconds": 4},
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )
        completed = _approve_asset_plan(
            manager,
            production_session_id=asset_review.production_session_id,
            adk_state=state,
        )

        self.assertEqual(completed.status, "completed")
        state_path = resolve_workspace_path(completed.state_ref or "")
        quality_json = state_path.parent / "quality_report.json"
        quality_md = state_path.parent / "quality_report.md"
        self.assertTrue(quality_json.exists())
        self.assertTrue(quality_md.exists())
        state_payload = json.loads(state_path.read_text(encoding="utf-8"))
        report = state_payload["quality_report"]
        check_by_id = {item["check_id"]: item for item in report["checks"]}
        self.assertEqual(check_by_id["no_subtitles_constraint"]["status"], "pass")
        self.assertEqual(check_by_id["audio_or_voiceover"]["status"], "pass")

        quality_view = asyncio.run(manager.view(
            production_session_id=completed.production_session_id,
            view_type="quality",
            adk_state=state,
        ))

        self.assertEqual(quality_view.view["view_type"], "quality")
        self.assertEqual(quality_view.view["quality_report"]["report_id"], report["report_id"])
        self.assertTrue(quality_view.view["quality_report_path"].endswith("quality_report.json"))

    def test_manager_product_ad_quality_report_checks_exposure_benefits_and_cta(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            provider_runtime=_FakeProviderRuntime(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="为猫粮产品做一个广告短视频，卖点是高含肉、无谷，结尾引导下单。",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "16:9", "duration_seconds": 4},
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )
        completed = _approve_asset_plan(
            manager,
            production_session_id=asset_review.production_session_id,
            adk_state=state,
        )

        state_payload = json.loads(resolve_workspace_path(completed.state_ref or "").read_text(encoding="utf-8"))
        report = state_payload["quality_report"]
        check_by_id = {item["check_id"]: item for item in report["checks"]}
        self.assertEqual(check_by_id["product_exposure"]["status"], "pass")
        self.assertEqual(check_by_id["product_benefit_coverage"]["status"], "pass")
        self.assertEqual(check_by_id["product_cta"]["status"], "pass")

    def test_manager_resume_revise_shot_review_returns_to_asset_plan_review(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            provider_runtime=_FakeProviderRuntime(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="make a clean product ad for a smart mug",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "9:16"},
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )
        shot_review = _approve_asset_plan_to_shot_review(
            manager,
            production_session_id=asset_review.production_session_id,
            adk_state=state,
        )
        self.assertEqual(shot_review.stage, "shot_review")

        result = asyncio.run(manager.resume(
            production_session_id=shot_review.production_session_id,
            user_response={"decision": "revise", "notes": "Keep the product larger in frame."},
            adk_state=state,
        ))

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "asset_plan_review")
        self.assertEqual(result.review_payload.review_type, "asset_plan_review")
        self.assertEqual(state["final_file_paths"], [])
        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(state_payload["asset_plan"]["status"], "draft")
        self.assertEqual(state_payload["shot_artifacts"][0]["status"], "stale")
        self.assertEqual(state_payload["shot_asset_plans"][0]["status"], "draft")
        self.assertIn("Keep the product larger in frame.", state_payload["brief_summary"])

    def test_manager_revises_only_current_shot_segment_before_regeneration(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            provider_runtime=_FakeProviderRuntime(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="make a 20 second product ad with three clear beats",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "9:16", "duration_seconds": 20},
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )
        first_review = _approve_asset_plan_to_shot_review(
            manager,
            production_session_id=asset_review.production_session_id,
            adk_state=state,
        )
        self.assertEqual(first_review.stage, "shot_review")
        second_review = _approve_shot_review(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )
        self.assertEqual(second_review.stage, "shot_review")
        before_revision = json.loads(resolve_workspace_path(second_review.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(len(before_revision["shot_asset_plans"]), 3)
        first_video_id = before_revision["shot_artifacts"][0]["video_asset_id"]
        second_video_id = before_revision["shot_artifacts"][1]["video_asset_id"]

        revised = asyncio.run(manager.resume(
            production_session_id=started.production_session_id,
            user_response={"decision": "revise", "notes": "Make this segment a tighter product close-up."},
            adk_state=state,
        ))

        self.assertEqual(revised.status, "needs_user_review")
        self.assertEqual(revised.stage, "asset_plan_review")
        revised_payload = json.loads(resolve_workspace_path(revised.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(revised_payload["shot_asset_plans"][0]["status"], "reviewed")
        self.assertEqual(revised_payload["shot_asset_plans"][1]["status"], "draft")
        self.assertEqual(revised_payload["shot_asset_plans"][2]["status"], "approved")
        self.assertEqual(revised_payload["shot_artifacts"][0]["status"], "approved")
        self.assertEqual(revised_payload["shot_artifacts"][1]["status"], "stale")
        first_video = next(item for item in revised_payload["asset_manifest"] if item["asset_id"] == first_video_id)
        second_video = next(item for item in revised_payload["asset_manifest"] if item["asset_id"] == second_video_id)
        self.assertEqual(first_video["status"], "valid")
        self.assertEqual(second_video["status"], "stale")
        self.assertIn(
            "Make this segment a tighter product close-up.",
            revised_payload["shot_asset_plans"][1]["visual_prompt"],
        )

        regenerated = _approve_asset_plan_to_shot_review(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )

        self.assertEqual(regenerated.status, "needs_user_review")
        self.assertEqual(regenerated.stage, "shot_review")
        regenerated_payload = json.loads(resolve_workspace_path(regenerated.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(regenerated_payload["shot_asset_plans"][0]["status"], "reviewed")
        self.assertEqual(regenerated_payload["shot_asset_plans"][1]["status"], "generated")
        self.assertEqual(regenerated_payload["shot_asset_plans"][2]["status"], "approved")
        self.assertEqual(regenerated_payload["shot_artifacts"][1]["status"], "stale")
        self.assertEqual(regenerated_payload["shot_artifacts"][2]["status"], "generated")

    def test_manager_targets_future_shot_plan_without_full_rebuild(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            provider_runtime=_FakeProviderRuntime(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="make a 20 second product ad with three clear beats",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "9:16", "duration_seconds": 20},
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )
        first_review = _approve_asset_plan_to_shot_review(
            manager,
            production_session_id=asset_review.production_session_id,
            adk_state=state,
        )
        first_payload = json.loads(resolve_workspace_path(first_review.state_ref or "").read_text(encoding="utf-8"))
        plan_ids_before = [item["shot_asset_plan_id"] for item in first_payload["shot_asset_plans"]]
        first_artifact_id = first_payload["shot_artifacts"][0]["shot_artifact_id"]

        revised = asyncio.run(manager.apply_revision(
            production_session_id=started.production_session_id,
            user_response={
                "targets": [{"kind": "shot_asset_plan", "id": plan_ids_before[2]}],
                "notes": "Make the closing segment a tighter pack shot.",
            },
            adk_state=state,
        ))

        self.assertEqual(revised.status, "needs_user_review")
        self.assertEqual(revised.stage, "asset_plan_review")
        revised_payload = json.loads(resolve_workspace_path(revised.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual([item["shot_asset_plan_id"] for item in revised_payload["shot_asset_plans"]], plan_ids_before)
        self.assertEqual(revised_payload["shot_asset_plans"][0]["status"], "generated")
        self.assertEqual(revised_payload["shot_asset_plans"][1]["status"], "approved")
        self.assertEqual(revised_payload["shot_asset_plans"][2]["status"], "draft")

        next_review = _approve_asset_plan_to_shot_review(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )

        self.assertEqual(next_review.status, "needs_user_review")
        self.assertEqual(next_review.stage, "shot_review")
        next_payload = json.loads(resolve_workspace_path(next_review.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual([item["shot_asset_plan_id"] for item in next_payload["shot_asset_plans"]], plan_ids_before)
        self.assertIn(first_artifact_id, {item["shot_artifact_id"] for item in next_payload["shot_artifacts"]})
        self.assertEqual(next_payload["shot_asset_plans"][0]["status"], "generated")
        self.assertEqual(next_payload["shot_asset_plans"][1]["status"], "generated")
        self.assertEqual(next_payload["shot_asset_plans"][2]["status"], "draft")

    def test_manager_resume_defaults_to_active_session_after_misc_work(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            provider_runtime=_FakeProviderRuntime(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="make a product ad that pauses for review",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "9:16"},
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )
        state["generated"].append({"name": "notes.txt", "path": "generated/misc/notes.txt"})
        state["uploaded"].append({"name": "unrelated.png", "path": "input/unrelated.png"})

        shot_review = asyncio.run(manager.resume(
            production_session_id=None,
            user_response={"decision": "approve"},
            adk_state=state,
        ))

        self.assertEqual(asset_review.stage, "asset_plan_review")
        self.assertEqual(shot_review.production_session_id, started.production_session_id)
        self.assertEqual(shot_review.status, "needs_user_review")
        self.assertEqual(shot_review.stage, "shot_review")

        result = asyncio.run(manager.resume(
            production_session_id=None,
            user_response={"decision": "approve"},
            adk_state=state,
        ))

        self.assertEqual(result.status, "completed")
        self.assertEqual(state["active_production_session_id"], started.production_session_id)
        self.assertEqual(state["uploaded"][0]["name"], "unrelated.png")

    def test_manager_analyze_revision_impact_is_read_only_for_completed_outputs(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            provider_runtime=_FakeProviderRuntime(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="make a product ad",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "9:16"},
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )
        completed = _approve_asset_plan(
            manager,
            production_session_id=asset_review.production_session_id,
            adk_state=state,
        )
        state_path = resolve_workspace_path(completed.state_ref or "")
        state_payload_before = state_path.read_text(encoding="utf-8")
        shot_id = json.loads(state_payload_before)["asset_plan"]["shot_plan"]["shot_id"]
        adk_state_before = json.dumps(state, sort_keys=True)

        result = asyncio.run(manager.analyze_revision_impact(
            production_session_id=completed.production_session_id,
            user_response={
                "targets": [{"kind": "shot", "id": shot_id}],
                "notes": "Change the shot pacing.",
            },
            adk_state=state,
        ))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.view["view_type"], "revision_impact")
        self.assertEqual(result.view["impact_level"], "generated_outputs_would_be_stale")
        self.assertEqual(result.view["state_mutation"], "none")
        impacted_kinds = {item["kind"] for item in result.view["impacted"]}
        self.assertTrue(
            {"asset_plan", "video_asset", "audio_asset", "timeline", "final_artifact"}.issubset(
                impacted_kinds
            )
        )
        self.assertEqual(json.dumps(state, sort_keys=True), adk_state_before)
        self.assertEqual(state_path.read_text(encoding="utf-8"), state_payload_before)

    def test_manager_apply_revision_marks_impacted_outputs_stale_and_pauses_for_review(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            provider_runtime=_FakeProviderRuntime(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="make a product ad",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "9:16"},
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )
        completed = _approve_asset_plan(
            manager,
            production_session_id=asset_review.production_session_id,
            adk_state=state,
        )
        state_path = resolve_workspace_path(completed.state_ref or "")
        completed_payload = json.loads(state_path.read_text(encoding="utf-8"))
        visual_prompt_before = completed_payload["asset_plan"]["shot_plan"]["visual_prompt"]
        generated_count = len(state["generated"])

        result = asyncio.run(manager.apply_revision(
            production_session_id=completed.production_session_id,
            user_response={
                "targets": [{"kind": "voiceover"}],
                "notes": "A warmer voiceover line.",
            },
            adk_state=state,
        ))

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "asset_plan_review")
        self.assertEqual(result.review_payload.review_type, "asset_plan_review")
        self.assertEqual(state["active_production_status"], "needs_user_review")
        self.assertEqual(len(state["generated"]), generated_count)
        state_payload = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state_payload["asset_plan"]["status"], "draft")
        self.assertEqual(
            state_payload["asset_plan"]["shot_plan"]["voiceover_text"],
            "A warmer voiceover line.",
        )
        self.assertEqual(
            state_payload["asset_plan"]["shot_plan"]["visual_prompt"],
            visual_prompt_before,
        )
        self.assertEqual(state_payload["asset_manifest"][0]["status"], "valid")
        self.assertEqual(state_payload["audio_manifest"][0]["status"], "stale")
        self.assertIsNone(state_payload["timeline"])
        self.assertIn("stale", state_payload["artifacts"][0]["description"].lower())

    def test_manager_apply_revision_then_approve_regenerates_version_safe_output(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            provider_runtime=_FakeProviderRuntime(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="make a product ad",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "9:16"},
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )
        first_completed = _approve_asset_plan(
            manager,
            production_session_id=asset_review.production_session_id,
            adk_state=state,
        )
        first_path = first_completed.artifacts[0].path
        first_payload = json.loads(resolve_workspace_path(first_completed.state_ref or "").read_text(encoding="utf-8"))
        first_plan_id = first_payload["asset_plan"]["plan_id"]
        first_video_id = first_payload["asset_manifest"][0]["asset_id"]
        first_audio_id = first_payload["audio_manifest"][0]["audio_id"]

        applied = asyncio.run(manager.apply_revision(
            production_session_id=first_completed.production_session_id,
            user_response={
                "targets": [{"kind": "voiceover"}],
                "notes": "Make the voiceover warmer.",
            },
            adk_state=state,
        ))
        applied_payload = json.loads(resolve_workspace_path(applied.state_ref or "").read_text(encoding="utf-8"))
        second_plan_id = applied_payload["asset_plan"]["plan_id"]

        second_shot_review = asyncio.run(manager.resume(
            production_session_id=started.production_session_id,
            user_response={"decision": "approve"},
            adk_state=state,
        ))
        self.assertEqual(second_shot_review.status, "needs_user_review")
        self.assertEqual(second_shot_review.stage, "shot_review")

        second_completed = _approve_shot_review(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )

        self.assertEqual(second_completed.status, "completed")
        self.assertNotEqual(second_plan_id, first_plan_id)
        self.assertNotEqual(second_completed.artifacts[0].path, first_path)
        self.assertEqual(state["final_file_paths"], [second_completed.artifacts[0].path])
        self.assertEqual(len(state["files_history"]), 4)
        state_payload = json.loads(resolve_workspace_path(second_completed.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(state_payload["asset_plan"]["plan_id"], second_plan_id)
        old_video = next(item for item in state_payload["asset_manifest"] if item["asset_id"] == first_video_id)
        old_audio = next(item for item in state_payload["audio_manifest"] if item["audio_id"] == first_audio_id)
        self.assertEqual(old_video["status"], "stale")
        self.assertEqual(old_audio["status"], "stale")
        latest_video = state_payload["asset_manifest"][-1]
        latest_audio = state_payload["audio_manifest"][-1]
        self.assertEqual(latest_video["status"], "valid")
        self.assertEqual(latest_audio["status"], "valid")
        self.assertEqual(state_payload["timeline"]["video_tracks"][0]["clips"][0]["asset_id"], latest_video["asset_id"])
        self.assertEqual(state_payload["timeline"]["audio_tracks"][0]["clips"][0]["audio_id"], latest_audio["audio_id"])

    def test_manager_apply_revision_targets_one_completed_shot_artifact(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            provider_runtime=_FakeProviderRuntime(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="make a 20 second product ad with three clear beats",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "9:16", "duration_seconds": 20},
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )
        completed = _approve_asset_plan(
            manager,
            production_session_id=asset_review.production_session_id,
            adk_state=state,
        )
        completed_payload = json.loads(resolve_workspace_path(completed.state_ref or "").read_text(encoding="utf-8"))
        second_artifact = completed_payload["shot_artifacts"][1]
        first_video_id = completed_payload["shot_artifacts"][0]["video_asset_id"]
        second_video_id = second_artifact["video_asset_id"]
        third_video_id = completed_payload["shot_artifacts"][2]["video_asset_id"]

        applied = asyncio.run(manager.apply_revision(
            production_session_id=completed.production_session_id,
            user_response={
                "targets": [{"kind": "shot_artifact", "id": second_artifact["shot_artifact_id"]}],
                "notes": "Regenerate only this segment with a clearer close-up.",
            },
            adk_state=state,
        ))

        self.assertEqual(applied.status, "needs_user_review")
        self.assertEqual(applied.stage, "asset_plan_review")
        applied_payload = json.loads(resolve_workspace_path(applied.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(applied_payload["shot_asset_plans"][0]["status"], "reviewed")
        self.assertEqual(applied_payload["shot_asset_plans"][1]["status"], "draft")
        self.assertEqual(applied_payload["shot_asset_plans"][2]["status"], "reviewed")
        self.assertEqual(applied_payload["shot_artifacts"][0]["status"], "approved")
        self.assertEqual(applied_payload["shot_artifacts"][1]["status"], "stale")
        self.assertEqual(applied_payload["shot_artifacts"][2]["status"], "approved")
        first_video = next(item for item in applied_payload["asset_manifest"] if item["asset_id"] == first_video_id)
        second_video = next(item for item in applied_payload["asset_manifest"] if item["asset_id"] == second_video_id)
        third_video = next(item for item in applied_payload["asset_manifest"] if item["asset_id"] == third_video_id)
        self.assertEqual(first_video["status"], "valid")
        self.assertEqual(second_video["status"], "stale")
        self.assertEqual(third_video["status"], "valid")
        self.assertIn("stale", applied_payload["artifacts"][0]["description"].lower())

        regenerated = _approve_asset_plan_to_shot_review(
            manager,
            production_session_id=completed.production_session_id,
            adk_state=state,
        )

        self.assertEqual(regenerated.status, "needs_user_review")
        self.assertEqual(regenerated.stage, "shot_review")
        regenerated_payload = json.loads(resolve_workspace_path(regenerated.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(regenerated_payload["shot_asset_plans"][0]["status"], "reviewed")
        self.assertEqual(regenerated_payload["shot_asset_plans"][1]["status"], "generated")
        self.assertEqual(regenerated_payload["shot_asset_plans"][2]["status"], "reviewed")
        self.assertEqual(regenerated_payload["shot_artifacts"][1]["status"], "stale")
        self.assertEqual(regenerated_payload["shot_artifacts"][3]["status"], "generated")

    def test_manager_apply_revision_rejects_unmatched_target_without_mutation(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            provider_runtime=_FakeProviderRuntime(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="make a product ad",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "9:16"},
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )
        completed = _approve_asset_plan(
            manager,
            production_session_id=asset_review.production_session_id,
            adk_state=state,
        )
        state_path = resolve_workspace_path(completed.state_ref or "")
        state_payload_before = state_path.read_text(encoding="utf-8")

        result = asyncio.run(manager.apply_revision(
            production_session_id=completed.production_session_id,
            user_response={
                "targets": [{"kind": "shot", "id": "shot_999"}],
                "notes": "Change a nonexistent shot.",
            },
            adk_state=state,
        ))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.error.code, "revision_target_unmatched")
        self.assertEqual(result.view["impact_level"], "target_unmatched")
        self.assertEqual(state_path.read_text(encoding="utf-8"), state_payload_before)

    def test_manager_add_reference_assets_marks_outputs_stale_without_duplicate_projection(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            provider_runtime=_FakeProviderRuntime(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="make a product ad",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "9:16"},
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )
        completed = _approve_asset_plan(
            manager,
            production_session_id=asset_review.production_session_id,
            adk_state=state,
        )
        generated_count = len(state["generated"])
        files_history_count = len(state["files_history"])
        final_file_paths = list(state["final_file_paths"])

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            reference_path = Path(tmpdir) / "new_product.png"
            reference_path.write_bytes(b"new-reference")
            result = asyncio.run(manager.add_reference_assets(
                production_session_id=completed.production_session_id,
                input_files=[
                    {
                        "name": "new_product.png",
                        "path": workspace_relative_path(reference_path),
                        "description": "new product angle",
                    }
                ],
                user_response=None,
                adk_state=state,
            ))

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "storyboard_review")
        self.assertEqual(state["active_production_status"], "needs_user_review")
        self.assertEqual(len(state["generated"]), generated_count)
        self.assertEqual(len(state["files_history"]), files_history_count)
        self.assertEqual(state["final_file_paths"], final_file_paths)
        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(state_payload["asset_manifest"][0]["status"], "stale")
        self.assertEqual(state_payload["audio_manifest"][0]["status"], "stale")
        self.assertIsNone(state_payload["timeline"])
        self.assertIsNone(state_payload["asset_plan"])
        self.assertEqual(state_payload["storyboard"]["status"], "draft")
        self.assertEqual(len(state_payload["storyboard"]["reference_asset_ids"]), 1)
        self.assertIn("stale", state_payload["artifacts"][0]["description"].lower())

    def test_manager_add_reference_assets_can_replace_existing_reference(self) -> None:
        state = _adk_state()
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            old_path = Path(tmpdir) / "old_product.png"
            new_path = Path(tmpdir) / "new_product.png"
            old_path.write_bytes(b"old-reference")
            new_path.write_bytes(b"new-reference")
            manager = ShortVideoProductionManager()
            started = asyncio.run(manager.start(
                user_prompt="make a product ad",
                input_files=[{"name": "old_product.png", "path": workspace_relative_path(old_path)}],
                placeholder_assets=False,
                render_settings={"aspect_ratio": "9:16"},
                adk_state=state,
            ))
            old_reference_id = started.view.get("storyboard", {}).get("reference_asset_ids", [])
            if not old_reference_id:
                state_payload = json.loads(resolve_workspace_path(started.state_ref or "").read_text(encoding="utf-8"))
                old_reference_id = state_payload["storyboard"]["reference_asset_ids"]

            result = asyncio.run(manager.add_reference_assets(
                production_session_id=started.production_session_id,
                input_files=[{"name": "new_product.png", "path": workspace_relative_path(new_path)}],
                user_response={"replace_reference_asset_id": old_reference_id[0]},
                adk_state=state,
            ))

        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        references = state_payload["reference_assets"]
        self.assertEqual(references[0]["status"], "replaced")
        self.assertEqual(references[0]["replaced_by"], references[1]["reference_asset_id"])
        self.assertEqual(state_payload["storyboard"]["reference_asset_ids"], [references[1]["reference_asset_id"]])

    def test_manager_start_accepts_string_input_file_paths(self) -> None:
        state = _adk_state()
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            reference_path = Path(tmpdir) / "product.png"
            reference_path.write_bytes(b"product-reference")
            manager = ShortVideoProductionManager()

            result = asyncio.run(manager.start(
                user_prompt="make a product ad",
                input_files=[workspace_relative_path(reference_path)],
                placeholder_assets=False,
                render_settings={"aspect_ratio": "9:16"},
                adk_state=state,
            ))

        self.assertEqual(result.status, "needs_user_review")
        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(len(state_payload["reference_assets"]), 1)
        self.assertEqual(state_payload["reference_assets"][0]["metadata"]["name"], "product.png")

    def test_manager_resume_approve_uses_default_seedance_native_audio_runtime(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="make a default-provider product ad",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "9:16"},
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )
        seedance_mock = AsyncMock(
            return_value={
                "status": "success",
                "message": b"seedance-video-with-audio",
                "provider": "seedance",
                "model_name": "doubao-seedance-2-0-260128",
                "generate_audio": True,
            }
        )

        with patch("src.production.short_video.providers.video_tools.seedance_video_generation_tool", seedance_mock):
            result = asyncio.run(manager.resume(
                production_session_id=asset_review.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            ))

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "shot_review")
        seedance_mock.assert_awaited_once()
        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(state_payload["asset_manifest"][0]["provider"], "seedance")
        self.assertEqual(state_payload["audio_manifest"][0]["provider"], "seedance_native_audio")
        self.assertTrue(state_payload["audio_manifest"][0]["metadata"]["native_audio"])
        self.assertTrue(seedance_mock.await_args.kwargs["generate_audio"])
        self.assertEqual(seedance_mock.await_args.kwargs["model_name"], "doubao-seedance-2-0-260128")

        completed = _approve_shot_review(
            manager,
            production_session_id=asset_review.production_session_id,
            adk_state=state,
        )
        self.assertEqual(completed.status, "completed")

    def test_manager_resume_approve_uses_seedance_fast_plan_settings(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="make a fast default-provider product ad",
            input_files=[],
            placeholder_assets=False,
            render_settings={
                "aspect_ratio": "9:16",
                "model_name": "doubao-seedance-2-0-fast-260128",
                "resolution": "1080p",
            },
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )
        seedance_mock = AsyncMock(
            return_value={
                "status": "success",
                "message": b"seedance-fast-video-with-audio",
                "provider": "seedance",
                "model_name": "doubao-seedance-2-0-fast-260128",
                "generate_audio": True,
            }
        )

        with patch("src.production.short_video.providers.video_tools.seedance_video_generation_tool", seedance_mock):
            result = asyncio.run(manager.resume(
                production_session_id=asset_review.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            ))

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "shot_review")
        self.assertEqual(seedance_mock.await_args.kwargs["model_name"], "doubao-seedance-2-0-fast-260128")
        self.assertEqual(seedance_mock.await_args.kwargs["resolution"], "720p")

        completed = _approve_shot_review(
            manager,
            production_session_id=asset_review.production_session_id,
            adk_state=state,
        )
        self.assertEqual(completed.status, "completed")

    def test_manager_resume_approve_uses_explicit_veo_tts_runtime(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="make a Veo+TTS product ad",
            input_files=[],
            placeholder_assets=False,
            render_settings={
                "provider": "veo_tts",
                "aspect_ratio": "9:16",
                "duration_seconds": 8,
            },
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )
        video_mock = AsyncMock(
            return_value={
                "status": "success",
                "message": b"veo-video",
                "provider": "veo",
                "model_name": "veo-3.1-generate-preview",
            }
        )
        tts_mock = AsyncMock(
            return_value={
                "status": "success",
                "message": b"tts-audio",
                "provider": "bytedance_tts",
                "model_name": "seed-tts-1.0",
                "speaker": "zh_female",
                "log_id": "log-1",
            }
        )
        seedance_mock = AsyncMock()

        with (
            patch("src.production.short_video.providers.video_tools.veo_video_generation_tool", video_mock),
            patch("src.production.short_video.providers.speech_tools.speech_synthesis_tool", tts_mock),
            patch("src.production.short_video.providers.video_tools.seedance_video_generation_tool", seedance_mock),
        ):
            result = asyncio.run(manager.resume(
                production_session_id=asset_review.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            ))

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "shot_review")
        video_mock.assert_awaited_once()
        tts_mock.assert_awaited_once()
        seedance_mock.assert_not_awaited()
        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(state_payload["asset_plan"]["planned_video_provider"], "veo")
        self.assertEqual(state_payload["asset_manifest"][0]["provider"], "veo")
        self.assertEqual(state_payload["audio_manifest"][0]["provider"], "bytedance_tts")
        self.assertEqual(video_mock.await_args.kwargs["duration_seconds"], 8)
        self.assertEqual(tts_mock.await_args.kwargs["text"], state_payload["shot_asset_plans"][0]["voiceover_text"])

    def test_manager_resume_cancel_marks_production_cancelled(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager()
        started = asyncio.run(manager.start(
            user_prompt="make a product ad",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "1:1"},
            adk_state=state,
        ))

        result = asyncio.run(manager.resume(
            production_session_id=started.production_session_id,
            user_response={"decision": "cancel"},
            adk_state=state,
        ))

        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.stage, "cancelled")
        self.assertEqual(state["active_production_status"], "cancelled")

    def test_manager_resume_revise_rebuilds_asset_plan_review(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager()
        started = asyncio.run(manager.start(
            user_prompt="make a calm product ad",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "16:9"},
            adk_state=state,
        ))
        asset_review = _approve_storyboard(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )

        result = asyncio.run(manager.resume(
            production_session_id=asset_review.production_session_id,
            user_response={"decision": "revise", "notes": "Make it energetic."},
            adk_state=state,
        ))

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "asset_plan_review")
        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        self.assertIn("Make it energetic.", state_payload["asset_plan"]["shot_plan"]["voiceover_text"])

    def test_manager_resume_revise_rebuilds_storyboard_review(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager()
        started = asyncio.run(manager.start(
            user_prompt="make a calm product ad",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "16:9"},
            adk_state=state,
        ))

        result = asyncio.run(manager.resume(
            production_session_id=started.production_session_id,
            user_response={"decision": "revise", "notes": "Make the hook energetic."},
            adk_state=state,
        ))

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "storyboard_review")
        self.assertEqual(result.review_payload.review_type, "storyboard_review")
        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        self.assertIn("Make the hook energetic.", state_payload["brief_summary"])
        self.assertIsNone(state_payload["asset_plan"])

    def test_manager_resume_accepts_plain_text_approval(self) -> None:
        state = _adk_state()
        manager = ShortVideoProductionManager(
            provider_runtime=_FakeProviderRuntime(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = asyncio.run(manager.start(
            user_prompt="make a product ad",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "9:16"},
            adk_state=state,
        ))

        result = asyncio.run(manager.resume(
            production_session_id=started.production_session_id,
            user_response="可以",
            adk_state=state,
        ))
        self.assertEqual(result.stage, "asset_plan_review")

        result = asyncio.run(manager.resume(
            production_session_id=started.production_session_id,
            user_response="可以",
            adk_state=state,
        ))

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "shot_review")

        result = asyncio.run(manager.resume(
            production_session_id=started.production_session_id,
            user_response="可以",
            adk_state=state,
        ))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.stage, "completed")

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

        self.assertEqual(result["status"], "needs_user_review")
        self.assertEqual(result["review_payload"]["review_type"], "storyboard_review")
        reference_item = next(
            item for item in result["review_payload"]["items"] if item["kind"] == "reference_assets"
        )
        self.assertEqual(reference_item["count"], 1)

    def test_tool_accepts_string_input_file_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            upload_path = Path(tmpdir) / "product.png"
            upload_path.write_bytes(b"fake-upload")
            state = _adk_state()
            tool_context = SimpleNamespace(state=state)

            result = asyncio.run(
                run_short_video_production(
                    action="start",
                    user_prompt="make a product ad from this package image",
                    input_files=[workspace_relative_path(upload_path)],
                    placeholder_assets=False,
                    render_settings={"aspect_ratio": "9:16"},
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result["status"], "needs_user_review")
        reference_item = next(
            item for item in result["review_payload"]["items"] if item["kind"] == "reference_assets"
        )
        self.assertEqual(reference_item["count"], 1)

    def test_tool_view_defaults_to_active_session(self) -> None:
        state = _adk_state()
        tool_context = SimpleNamespace(state=state)
        started = asyncio.run(
            run_short_video_production(
                action="start",
                user_prompt="make a product ad",
                placeholder_assets=False,
                render_settings={"aspect_ratio": "9:16"},
                tool_context=tool_context,
            )
        )

        result = asyncio.run(
            run_short_video_production(
                action="view",
                view_type="overview",
                tool_context=tool_context,
            )
        )

        self.assertEqual(result["status"], "needs_user_review")
        self.assertEqual(result["production_session_id"], started["production_session_id"])
        self.assertEqual(result["view"]["view_type"], "overview")
        self.assertEqual(result["view"]["counts"]["events"], 2)

    def test_tool_add_reference_assets_uses_uploaded_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            upload_path = Path(tmpdir) / "product.png"
            upload_path.write_bytes(b"fake-upload")
            state = _adk_state()
            tool_context = SimpleNamespace(state=state)
            started = asyncio.run(
                run_short_video_production(
                    action="start",
                    user_prompt="make a product ad",
                    placeholder_assets=False,
                    render_settings={"aspect_ratio": "9:16"},
                    tool_context=tool_context,
                )
            )
            state["uploaded"] = [
                {
                    "name": "product.png",
                    "path": workspace_relative_path(upload_path),
                    "description": "product reference",
                    "source": "channel",
                }
            ]
            result = asyncio.run(
                run_short_video_production(
                    action="add_reference_assets",
                    production_session_id=started["production_session_id"],
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result["status"], "needs_user_review")
        self.assertEqual(result["review_payload"]["review_type"], "storyboard_review")
        reference_item = next(item for item in result["review_payload"]["items"] if item["kind"] == "reference_assets")
        self.assertEqual(reference_item["count"], 1)

    def test_tool_add_reference_assets_accepts_string_input_file_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            upload_path = Path(tmpdir) / "product.png"
            upload_path.write_bytes(b"fake-upload")
            state = _adk_state()
            tool_context = SimpleNamespace(state=state)
            started = asyncio.run(
                run_short_video_production(
                    action="start",
                    user_prompt="make a product ad",
                    placeholder_assets=False,
                    render_settings={"aspect_ratio": "9:16"},
                    tool_context=tool_context,
                )
            )

            result = asyncio.run(
                run_short_video_production(
                    action="add_reference_assets",
                    production_session_id=started["production_session_id"],
                    input_files=[workspace_relative_path(upload_path)],
                    tool_context=tool_context,
                )
            )

        self.assertEqual(result["status"], "needs_user_review")
        reference_item = next(item for item in result["review_payload"]["items"] if item["kind"] == "reference_assets")
        self.assertEqual(reference_item["count"], 1)

    def test_tool_analyze_revision_impact_reports_unmatched_target(self) -> None:
        state = _adk_state()
        tool_context = SimpleNamespace(state=state)
        started = asyncio.run(
            run_short_video_production(
                action="start",
                user_prompt="make a product ad",
                placeholder_assets=False,
                render_settings={"aspect_ratio": "9:16"},
                tool_context=tool_context,
            )
        )

        result = asyncio.run(
            run_short_video_production(
                action="analyze_revision_impact",
                user_response={
                    "targets": [{"kind": "shot", "id": "shot_3"}],
                    "notes": "Only change the third shot.",
                },
                tool_context=tool_context,
            )
        )

        self.assertEqual(result["status"], "needs_user_review")
        self.assertEqual(result["production_session_id"], started["production_session_id"])
        self.assertEqual(result["view"]["view_type"], "revision_impact")
        self.assertEqual(result["view"]["impact_level"], "target_unmatched")
        self.assertEqual(result["view"]["unmatched_targets"][0]["id"], "shot_3")
        available_kinds = {item["kind"] for item in result["view"]["available_targets"]}
        self.assertIn("shot", available_kinds)

    def test_tool_analyze_revision_impact_accepts_plain_text_user_response(self) -> None:
        state = _adk_state()
        tool_context = SimpleNamespace(state=state)
        asyncio.run(
            run_short_video_production(
                action="start",
                user_prompt="make a product ad",
                placeholder_assets=False,
                render_settings={"aspect_ratio": "16:9"},
                tool_context=tool_context,
            )
        )

        result = asyncio.run(
            run_short_video_production(
                action="analyze_revision_impact",
                user_response="Only change the current voiceover. Do not apply it yet.",
                tool_context=tool_context,
            )
        )

        self.assertEqual(result["status"], "needs_user_review")
        self.assertEqual(result["view"]["view_type"], "revision_impact")
        self.assertEqual(
            result["view"]["revision_request"]["notes"],
            "Only change the current voiceover. Do not apply it yet.",
        )
        self.assertEqual(result["view"]["state_mutation"], "none")

    def test_tool_apply_revision_defaults_to_active_session(self) -> None:
        state = _adk_state()
        tool_context = SimpleNamespace(state=state)
        started = asyncio.run(
            run_short_video_production(
                action="start",
                user_prompt="make a product ad",
                placeholder_assets=False,
                render_settings={"aspect_ratio": "16:9"},
                tool_context=tool_context,
            )
        )

        result = asyncio.run(
            run_short_video_production(
                action="apply_revision",
                user_response={
                    "targets": [{"kind": "voiceover"}],
                    "notes": "Make the voiceover shorter.",
                },
                tool_context=tool_context,
            )
        )

        self.assertEqual(result["status"], "needs_user_review")
        self.assertEqual(result["production_session_id"], started["production_session_id"])
        self.assertEqual(result["review_payload"]["review_type"], "storyboard_review")
        self.assertEqual(
            result["view"]["revision_request"]["notes"],
            "Make the voiceover shorter.",
        )

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


def _provider_asset_plan(selected_ratio: str = "9:16") -> ShortVideoAssetPlan:
    return ShortVideoAssetPlan(
        selected_ratio=selected_ratio,  # type: ignore[arg-type]
        duration_seconds=8,
        shot_plan=ShortVideoShotPlan(
            visual_prompt="Show a polished product ad.",
            voiceover_text="Meet the product.",
        ),
    )


class ShortVideoProviderRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_routed_provider_runtime_uses_selected_provider_without_fallback(self) -> None:
        seedance_runtime = SimpleNamespace(
            generate_video_clip=AsyncMock(return_value="seedance-video"),
            synthesize_voiceover=AsyncMock(return_value="seedance-audio"),
        )
        veo_runtime = SimpleNamespace(
            generate_video_clip=AsyncMock(return_value="veo-video"),
            synthesize_voiceover=AsyncMock(return_value="veo-audio"),
        )
        provider = RoutedShortVideoProviderRuntime(
            seedance_runtime=seedance_runtime,
            veo_tts_runtime=veo_runtime,
        )
        asset_plan = _provider_asset_plan()
        asset_plan.planned_video_provider = "veo"
        settings = ShortVideoRenderSettings(aspect_ratio="9:16", width=720, height=1280)

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            video_result = await provider.generate_video_clip(
                session_root=Path(tmpdir),
                asset_plan=asset_plan,
                render_settings=settings,
                reference_assets=[],
                owner_ref=ProductionOwnerRef(sender_id="user-1"),
            )
            audio_result = await provider.synthesize_voiceover(
                session_root=Path(tmpdir),
                asset_plan=asset_plan,
                render_settings=settings,
                owner_ref=ProductionOwnerRef(sender_id="user-1"),
            )

        self.assertEqual(video_result, "veo-video")
        self.assertEqual(audio_result, "veo-audio")
        veo_runtime.generate_video_clip.assert_awaited_once()
        veo_runtime.synthesize_voiceover.assert_awaited_once()
        seedance_runtime.generate_video_clip.assert_not_awaited()
        seedance_runtime.synthesize_voiceover.assert_not_awaited()

    async def test_seedance_native_audio_runtime_writes_video_and_reuses_native_audio(self) -> None:
        provider = SeedanceNativeAudioProviderRuntime()
        asset_plan = _provider_asset_plan()
        settings = ShortVideoRenderSettings(aspect_ratio="9:16", width=720, height=1280)
        owner_ref = ProductionOwnerRef(channel="cli", chat_id="terminal", sender_id="user-1")
        seedance_mock = AsyncMock(
            return_value={
                "status": "success",
                "message": b"seedance-video-bytes",
                "provider": "seedance",
                "model_name": "doubao-seedance-2-0-260128",
                "generate_audio": True,
            }
        )

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            session_root = Path(tmpdir)
            with patch(
                "src.production.short_video.providers.video_tools.seedance_video_generation_tool",
                seedance_mock,
            ):
                video_asset = await provider.generate_video_clip(
                    session_root=session_root,
                    asset_plan=asset_plan,
                    render_settings=settings,
                    reference_assets=[],
                    owner_ref=owner_ref,
                )
                audio_asset = await provider.synthesize_voiceover(
                    session_root=session_root,
                    asset_plan=asset_plan,
                    render_settings=settings,
                    owner_ref=owner_ref,
                )

            self.assertEqual(resolve_workspace_path(video_asset.path).read_bytes(), b"seedance-video-bytes")
            self.assertEqual(audio_asset.path, video_asset.path)
            self.assertEqual(video_asset.provider, "seedance")
            self.assertEqual(audio_asset.provider, "seedance_native_audio")
            self.assertTrue(video_asset.metadata["native_audio"])
            self.assertTrue(audio_asset.metadata["native_audio"])
            self.assertEqual(seedance_mock.await_args.kwargs["mode"], "prompt")
            self.assertEqual(seedance_mock.await_args.kwargs["aspect_ratio"], "9:16")
            self.assertEqual(seedance_mock.await_args.kwargs["resolution"], "720p")
            self.assertEqual(seedance_mock.await_args.kwargs["duration_seconds"], 8)
            self.assertTrue(seedance_mock.await_args.kwargs["generate_audio"])

    async def test_seedance_native_audio_runtime_uses_asset_plan_model_settings(self) -> None:
        provider = SeedanceNativeAudioProviderRuntime()
        asset_plan = _provider_asset_plan()
        asset_plan.planned_video_model_name = "doubao-seedance-2-0-fast-260128"
        asset_plan.planned_video_resolution = "480p"
        settings = ShortVideoRenderSettings(aspect_ratio="9:16", width=720, height=1280)
        seedance_mock = AsyncMock(
            return_value={
                "status": "success",
                "message": b"seedance-fast-video-bytes",
                "provider": "seedance",
                "model_name": "doubao-seedance-2-0-fast-260128",
                "generate_audio": True,
            }
        )

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            with patch(
                "src.production.short_video.providers.video_tools.seedance_video_generation_tool",
                seedance_mock,
            ):
                await provider.generate_video_clip(
                    session_root=Path(tmpdir),
                    asset_plan=asset_plan,
                    render_settings=settings,
                    reference_assets=[],
                    owner_ref=ProductionOwnerRef(sender_id="user-1"),
                )

        self.assertEqual(seedance_mock.await_args.kwargs["model_name"], "doubao-seedance-2-0-fast-260128")
        self.assertEqual(seedance_mock.await_args.kwargs["resolution"], "480p")

    async def test_veo_tts_runtime_writes_provider_outputs(self) -> None:
        provider = VeoTtsProviderRuntime()
        asset_plan = _provider_asset_plan()
        settings = ShortVideoRenderSettings(aspect_ratio="9:16", width=720, height=1280)
        owner_ref = ProductionOwnerRef(channel="cli", chat_id="terminal", sender_id="user-1")
        video_mock = AsyncMock(
            return_value={
                "status": "success",
                "message": b"video-bytes",
                "provider": "veo",
                "model_name": "veo-3.1-generate-preview",
            }
        )
        tts_mock = AsyncMock(
            return_value={
                "status": "success",
                "message": b"audio-bytes",
                "provider": "bytedance_tts",
                "model_name": "seed-tts-2.0",
                "speaker": "zh_female_vv_uranus_bigtts",
                "voice_name": "Vivi 2.0",
                "log_id": "log-1",
            }
        )

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            session_root = Path(tmpdir)
            with (
                patch(
                    "src.production.short_video.providers.video_tools.veo_video_generation_tool",
                    video_mock,
                ),
                patch(
                    "src.production.short_video.providers.speech_tools.speech_synthesis_tool",
                    tts_mock,
                ),
            ):
                video_asset = await provider.generate_video_clip(
                    session_root=session_root,
                    asset_plan=asset_plan,
                    render_settings=settings,
                    reference_assets=[],
                    owner_ref=owner_ref,
                )
                audio_asset = await provider.synthesize_voiceover(
                    session_root=session_root,
                    asset_plan=asset_plan,
                    render_settings=settings,
                    owner_ref=owner_ref,
                )

            self.assertEqual(resolve_workspace_path(video_asset.path).read_bytes(), b"video-bytes")
            self.assertEqual(resolve_workspace_path(audio_asset.path).read_bytes(), b"audio-bytes")
            self.assertEqual(video_asset.provider, "veo")
            self.assertEqual(audio_asset.provider, "bytedance_tts")
            self.assertEqual(video_asset.metadata["model_name"], "veo-3.1-generate-preview")
            self.assertEqual(audio_asset.metadata["speaker"], "zh_female_vv_uranus_bigtts")
            self.assertEqual(audio_asset.metadata["voice_name"], "Vivi 2.0")
            self.assertEqual(video_mock.await_args.kwargs["aspect_ratio"], "9:16")
            self.assertEqual(video_mock.await_args.kwargs["duration_seconds"], 8)
            self.assertEqual(video_mock.await_args.kwargs["mode"], "prompt")
            self.assertEqual(tts_mock.await_args.kwargs["user_id"], "user-1")
            self.assertEqual(tts_mock.await_args.kwargs["text"], "Meet the product.")

    async def test_veo_tts_runtime_rejects_square_ratio_before_provider_call(self) -> None:
        provider = VeoTtsProviderRuntime()
        asset_plan = _provider_asset_plan(selected_ratio="1:1")
        settings = ShortVideoRenderSettings(aspect_ratio="1:1", width=1024, height=1024)

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            with self.assertRaisesRegex(ShortVideoProviderError, "16:9 or 9:16"):
                await provider.generate_video_clip(
                    session_root=Path(tmpdir),
                    asset_plan=asset_plan,
                    render_settings=settings,
                    reference_assets=[],
                    owner_ref=ProductionOwnerRef(sender_id="user-1"),
                )


class ShortVideoProductionAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_requires_context(self) -> None:
        result = await run_short_video_production(action="start", placeholder_assets=True)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["stage"], "missing_tool_context")


class ShortVideoProductionProgressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        configure_step_event_publisher(None)

    async def test_manager_resume_publishes_internal_progress_events(self) -> None:
        messages = []

        async def _publisher(message):
            messages.append(message)

        configure_step_event_publisher(_publisher)
        state = _adk_state()
        manager = ShortVideoProductionManager(
            provider_runtime=_FakeProviderRuntime(),
            renderer=_FakeRenderer(),
            validator=_FakeValidator(),
        )
        started = await manager.start(
            user_prompt="make a product ad",
            input_files=[],
            placeholder_assets=False,
            render_settings={"aspect_ratio": "9:16"},
            adk_state=state,
        )
        asset_review = await _approve_storyboard_async(
            manager,
            production_session_id=started.production_session_id,
            adk_state=state,
        )

        with route_context("feishu", "chat-short-video"):
            result = await manager.resume(
                production_session_id=asset_review.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
            await asyncio.sleep(0)
            completed = await manager.resume(
                production_session_id=asset_review.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            )
            await asyncio.sleep(0)

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "shot_review")
        self.assertEqual(completed.status, "completed")
        stage_titles = [message.metadata["stage_title"] for message in messages]
        self.assertIn("Generating Shot Segment", stage_titles)
        self.assertIn("Preparing Segment Audio", stage_titles)
        self.assertIn("Shot Segment Ready", stage_titles)
        self.assertIn("Rendering Final Short Video", stage_titles)
        self.assertIn("Short Video Completed", stage_titles)


@unittest.skipUnless(_HAS_FFMPEG, "ffmpeg/ffprobe not available")
class ShortVideoProductionFfmpegTests(unittest.TestCase):
    def test_real_p0a_placeholder_render_outputs_playable_mp4(self) -> None:
        state = _adk_state()
        state["sid"] = "session_short_video_ffmpeg_test"
        manager = ShortVideoProductionManager()

        result = asyncio.run(manager.start(
            user_prompt="make a real placeholder video",
            input_files=[],
            placeholder_assets=True,
            render_settings={"aspect_ratio": "1:1", "duration_seconds": 1},
            adk_state=state,
        ))

        self.assertEqual(result.status, "completed")
        final_path = resolve_workspace_path(result.artifacts[0].path)
        self.assertTrue(final_path.exists())
        self.assertGreater(final_path.stat().st_size, 0)
