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
from src.production.short_video.providers import ShortVideoProviderError, VeoTtsProviderRuntime
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

    def test_manager_start_p0b_returns_asset_plan_review(self) -> None:
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
        self.assertEqual(result.stage, "asset_plan_review")
        self.assertEqual(result.review_payload.review_type, "asset_plan_review")
        self.assertEqual(state["active_production_session_id"], result.production_session_id)
        self.assertEqual(state["active_production_status"], "needs_user_review")
        state_path = resolve_workspace_path(result.state_ref or "")
        asset_plan_payload = json.loads((state_path.parent / "asset_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(asset_plan_payload["asset_plan"]["planned_video_provider"], "veo")
        self.assertIsNone(asset_plan_payload["asset_plan"]["selected_ratio"])
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
                "social media short",
                {"aspect_ratio": "9:16"},
            ),
            (
                "帮我做一支 15 秒短视频，先给计划",
                "social_media_short",
                "social media short",
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
                asset_plan = state_payload["asset_plan"]
                self.assertEqual(asset_plan["video_type"], expected_video_type)
                self.assertIn(prompt_marker, asset_plan["shot_plan"]["visual_prompt"])
                review_items = result.review_payload.model_dump(mode="json")["items"]
                video_type_item = next(item for item in review_items if item["kind"] == "video_type")
                self.assertEqual(video_type_item["video_type"], expected_video_type)

    def test_manager_view_returns_asset_plan_without_mutating_adk_state(self) -> None:
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
            view_type="asset_plan",
            adk_state=state,
        ))

        self.assertEqual(result.status, "needs_user_review")
        self.assertEqual(result.stage, "asset_plan_review")
        self.assertEqual(result.view["view_type"], "asset_plan")
        self.assertEqual(result.view["asset_plan"]["planned_video_provider"], "veo")
        self.assertEqual(result.view["active_review"]["review_type"], "asset_plan_review")
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

        result = asyncio.run(manager.resume(
            production_session_id=started.production_session_id,
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

        result = asyncio.run(manager.resume(
            production_session_id=started.production_session_id,
            user_response={"decision": "approve", "selected_ratio": "9:16"},
            adk_state=state,
        ))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.stage, "completed")
        self.assertEqual(state["final_file_paths"], [result.artifacts[0].path])
        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(state_payload["asset_plan"]["selected_ratio"], "9:16")
        self.assertEqual(state_payload["asset_manifest"][0]["provider"], "veo")
        self.assertEqual(state_payload["audio_manifest"][0]["provider"], "mock_tts")

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
        state["generated"].append({"name": "notes.txt", "path": "generated/misc/notes.txt"})
        state["uploaded"].append({"name": "unrelated.png", "path": "input/unrelated.png"})

        result = asyncio.run(manager.resume(
            production_session_id=None,
            user_response={"decision": "approve"},
            adk_state=state,
        ))

        self.assertEqual(result.production_session_id, started.production_session_id)
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
        completed = asyncio.run(manager.resume(
            production_session_id=started.production_session_id,
            user_response={"decision": "approve"},
            adk_state=state,
        ))
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
        completed = asyncio.run(manager.resume(
            production_session_id=started.production_session_id,
            user_response={"decision": "approve"},
            adk_state=state,
        ))
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
        first_completed = asyncio.run(manager.resume(
            production_session_id=started.production_session_id,
            user_response={"decision": "approve"},
            adk_state=state,
        ))
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

        second_completed = asyncio.run(manager.resume(
            production_session_id=started.production_session_id,
            user_response={"decision": "approve"},
            adk_state=state,
        ))

        self.assertEqual(second_completed.status, "completed")
        self.assertNotEqual(second_plan_id, first_plan_id)
        self.assertNotEqual(second_completed.artifacts[0].path, first_path)
        self.assertEqual(state["final_file_paths"], [second_completed.artifacts[0].path])
        self.assertEqual(len(state["files_history"]), 2)
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
        completed = asyncio.run(manager.resume(
            production_session_id=started.production_session_id,
            user_response={"decision": "approve"},
            adk_state=state,
        ))
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
        completed = asyncio.run(manager.resume(
            production_session_id=started.production_session_id,
            user_response={"decision": "approve"},
            adk_state=state,
        ))
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
        self.assertEqual(result.stage, "asset_plan_review")
        self.assertEqual(state["active_production_status"], "needs_user_review")
        self.assertEqual(len(state["generated"]), generated_count)
        self.assertEqual(len(state["files_history"]), files_history_count)
        self.assertEqual(state["final_file_paths"], final_file_paths)
        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        self.assertEqual(state_payload["asset_manifest"][0]["status"], "stale")
        self.assertEqual(state_payload["audio_manifest"][0]["status"], "stale")
        self.assertIsNone(state_payload["timeline"])
        self.assertEqual(state_payload["asset_plan"]["status"], "draft")
        self.assertEqual(len(state_payload["asset_plan"]["reference_asset_ids"]), 1)
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
            old_reference_id = started.view.get("asset_plan", {}).get("reference_asset_ids", [])
            if not old_reference_id:
                state_payload = json.loads(resolve_workspace_path(started.state_ref or "").read_text(encoding="utf-8"))
                old_reference_id = state_payload["asset_plan"]["reference_asset_ids"]

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
        self.assertEqual(state_payload["asset_plan"]["reference_asset_ids"], [references[1]["reference_asset_id"]])

    def test_manager_resume_approve_uses_default_veo_tts_runtime(self) -> None:
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
        video_mock = AsyncMock(
            return_value={"status": "success", "message": b"video", "provider": "veo"}
        )
        tts_mock = AsyncMock(
            return_value={"status": "success", "message": b"audio", "provider": "bytedance_tts"}
        )

        with (
            patch("src.production.short_video.providers.video_tools.veo_video_generation_tool", video_mock),
            patch("src.production.short_video.providers.speech_tools.speech_synthesis_tool", tts_mock),
        ):
            result = asyncio.run(manager.resume(
                production_session_id=started.production_session_id,
                user_response={"decision": "approve"},
                adk_state=state,
            ))

        self.assertEqual(result.status, "completed")
        video_mock.assert_awaited_once()
        tts_mock.assert_awaited_once()

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

        result = asyncio.run(manager.resume(
            production_session_id=started.production_session_id,
            user_response={"decision": "revise", "notes": "Make it energetic."},
            adk_state=state,
        ))

        self.assertEqual(result.status, "needs_user_review")
        state_payload = json.loads(resolve_workspace_path(result.state_ref or "").read_text(encoding="utf-8"))
        self.assertIn("Make it energetic.", state_payload["asset_plan"]["shot_plan"]["voiceover_text"])

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
        self.assertEqual(result["review_payload"]["review_type"], "asset_plan_review")
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
        self.assertEqual(result["review_payload"]["review_type"], "asset_plan_review")
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
        self.assertEqual(result["review_payload"]["review_type"], "asset_plan_review")
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
                "model_name": "seed-tts-1.0",
                "speaker": "zh_female",
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
            self.assertEqual(audio_asset.metadata["speaker"], "zh_female")
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
