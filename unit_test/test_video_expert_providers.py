import unittest
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.experts.video_generation import tool as video_tools
from src.agents.experts.video_generation.video_generation_agent import VideoGenerationAgent
from src.runtime.workspace import workspace_root


def _build_ctx(state: dict) -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(
            state=state,
            app_name="test_app",
            user_id="user_1",
            id="session_1",
        ),
    )


class VideoExpertProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_generation_uses_seedance_by_default(self) -> None:
        agent = VideoGenerationAgent(name="VideoGenerationAgent")
        ctx = _build_ctx({"current_parameters": {"prompt": "draw a cat video"}, "step": 0})

        with (
            patch(
                "src.agents.experts.video_generation.video_generation_agent.video_tools.prompt_enhancement_tool",
                new=AsyncMock(return_value={"status": "success", "message": "enhanced cat video"}),
            ),
            patch(
                "src.agents.experts.video_generation.video_generation_agent.save_binary_output",
                return_value=workspace_root() / "generated" / "session_1" / "step1_video_generation_output0.mp4",
            ),
            patch(
                "src.agents.experts.video_generation.video_generation_agent.video_tools.seedance_video_generation_tool",
                new=AsyncMock(
                    return_value={
                        "status": "success",
                        "message": b"video-data",
                        "provider": "seedance",
                        "model_name": "doubao-seedance-1-0-pro-250528",
                    }
                ),
            ) as seedance_mock,
            patch(
                "src.agents.experts.video_generation.video_generation_agent.video_tools.veo_video_generation_tool",
                new=AsyncMock(),
            ) as veo_mock,
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        seedance_mock.assert_awaited_once()
        veo_mock.assert_not_called()

    async def test_video_generation_uses_veo_when_requested(self) -> None:
        agent = VideoGenerationAgent(name="VideoGenerationAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "prompt": "draw a cat video",
                    "provider": "veo",
                    "resolution": "1080p",
                },
                "step": 0,
            }
        )

        with (
            patch(
                "src.agents.experts.video_generation.video_generation_agent.video_tools.prompt_enhancement_tool",
                new=AsyncMock(return_value={"status": "success", "message": "enhanced cat video"}),
            ),
            patch(
                "src.agents.experts.video_generation.video_generation_agent.save_binary_output",
                return_value=workspace_root() / "generated" / "session_1" / "step1_video_generation_output0.mp4",
            ),
            patch(
                "src.agents.experts.video_generation.video_generation_agent.video_tools.seedance_video_generation_tool",
                new=AsyncMock(),
            ) as seedance_mock,
            patch(
                "src.agents.experts.video_generation.video_generation_agent.video_tools.veo_video_generation_tool",
                new=AsyncMock(
                    return_value={
                        "status": "success",
                        "message": b"video-data",
                        "provider": "veo",
                        "model_name": "veo-3.1-generate-preview",
                    }
                ),
            ) as veo_mock,
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        veo_mock.assert_awaited_once_with(
            "enhanced cat video",
            input_paths=[],
            mode="prompt",
            aspect_ratio="16:9",
            resolution="1080p",
            duration_seconds=8,
            negative_prompt="",
            person_generation=None,
            seed=None,
            enhance_prompt=None,
        )
        seedance_mock.assert_not_called()

    async def test_video_generation_skips_local_prompt_enhancement_when_veo_enhance_prompt_is_false(self) -> None:
        agent = VideoGenerationAgent(name="VideoGenerationAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "prompt": "draw a cat video",
                    "provider": "veo",
                    "enhance_prompt": False,
                },
                "step": 0,
            }
        )

        with (
            patch(
                "src.agents.experts.video_generation.video_generation_agent.video_tools.prompt_enhancement_tool",
                new=AsyncMock(),
            ) as enhancement_mock,
            patch(
                "src.agents.experts.video_generation.video_generation_agent.save_binary_output",
                return_value=workspace_root() / "generated" / "session_1" / "step1_video_generation_output0.mp4",
            ),
            patch(
                "src.agents.experts.video_generation.video_generation_agent.video_tools.veo_video_generation_tool",
                new=AsyncMock(
                    return_value={
                        "status": "success",
                        "message": b"video-data",
                        "provider": "veo",
                        "model_name": "veo-3.1-generate-preview",
                    }
                ),
            ) as veo_mock,
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        enhancement_mock.assert_not_called()
        veo_mock.assert_awaited_once_with(
            "draw a cat video",
            input_paths=[],
            mode="prompt",
            aspect_ratio="16:9",
            resolution="720p",
            duration_seconds=8,
            negative_prompt="",
            person_generation=None,
            seed=None,
            enhance_prompt=False,
        )

    async def test_video_generation_reports_output_artifact_name_in_message(self) -> None:
        agent = VideoGenerationAgent(name="VideoGenerationAgent")
        ctx = _build_ctx({"current_parameters": {"prompt": "draw a cat video"}, "step": 0})

        with (
            patch(
                "src.agents.experts.video_generation.video_generation_agent.video_tools.prompt_enhancement_tool",
                new=AsyncMock(return_value={"status": "success", "message": "enhanced cat video"}),
            ),
            patch(
                "src.agents.experts.video_generation.video_generation_agent.save_binary_output",
                return_value=workspace_root() / "generated" / "session_1" / "step1_video_generation_output0.mp4",
            ),
            patch(
                "src.agents.experts.video_generation.video_generation_agent.video_tools.seedance_video_generation_tool",
                new=AsyncMock(
                    return_value={
                        "status": "success",
                        "message": b"video-data",
                        "provider": "seedance",
                        "model_name": "doubao-seedance-1-0-pro-250528",
                    }
                ),
            ),
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertIn("step1_video_generation_output0.mp4", current_output["message"])
        self.assertEqual(
            current_output["output_files"][0]["path"],
            "generated/session_1/step1_video_generation_output0.mp4",
        )


class VideoGenerationToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_seedance_tool_uses_top_level_ratio_argument(self) -> None:
        create_mock = MagicMock(return_value=SimpleNamespace(id="task-1"))
        get_mock = MagicMock(return_value=SimpleNamespace(status="failed", error="mock error"))
        fake_client = SimpleNamespace(
            content_generation=SimpleNamespace(
                tasks=SimpleNamespace(
                    create=create_mock,
                    get=get_mock,
                )
            )
        )
        fake_module = SimpleNamespace(Ark=MagicMock(return_value=fake_client))

        with (
            patch.dict(os.environ, {"ARK_API_KEY": "test-key"}, clear=False),
            patch.dict(sys.modules, {"volcenginesdkarkruntime": fake_module}),
        ):
            result = await video_tools.seedance_video_generation_tool(
                "draw a cat video",
                aspect_ratio="9:16",
            )

        self.assertEqual(result["status"], "error")
        fake_module.Ark.assert_called_once_with(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key="test-key",
        )
        create_mock.assert_called_once()
        self.assertEqual(
            create_mock.call_args.kwargs,
            {
                "model": "doubao-seedance-1-0-pro-250528",
                "content": [{"type": "text", "text": "draw a cat video"}],
                "ratio": "9:16",
            },
        )

    async def test_veo_tool_uses_updated_preview_model(self) -> None:
        generate_videos_mock = AsyncMock(
            return_value=SimpleNamespace(
                done=True,
                result=SimpleNamespace(
                    generated_videos=[SimpleNamespace(video="video-file-id")]
                ),
            )
        )
        download_mock = AsyncMock(return_value=b"video-data")
        fake_client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_videos=generate_videos_mock),
                operations=SimpleNamespace(get=AsyncMock()),
                files=SimpleNamespace(download=download_mock),
            )
        )

        with (
            patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}, clear=False),
            patch("src.agents.experts.video_generation.tool.genai.Client", return_value=fake_client),
        ):
            result = await video_tools.veo_video_generation_tool(
                "draw a cat video",
                resolution="1080p",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["model_name"], "veo-3.1-generate-preview")
        generate_videos_mock.assert_awaited_once()
        self.assertEqual(
            generate_videos_mock.await_args.kwargs["model"],
            "veo-3.1-generate-preview",
        )
        config = generate_videos_mock.await_args.kwargs["config"]
        self.assertEqual(config.duration_seconds, 8)
        self.assertEqual(config.resolution, "1080p")
        download_mock.assert_awaited_once_with(file="video-file-id")

    async def test_veo_tool_accepts_gemini_api_key_fallback(self) -> None:
        generate_videos_mock = AsyncMock(
            return_value=SimpleNamespace(
                done=True,
                result=SimpleNamespace(
                    generated_videos=[SimpleNamespace(video="video-file-id")]
                ),
            )
        )
        fake_client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_videos=generate_videos_mock),
                operations=SimpleNamespace(get=AsyncMock()),
                files=SimpleNamespace(download=AsyncMock(return_value=b"video-data")),
            )
        )

        with (
            patch.dict(
                os.environ,
                {"GOOGLE_API_KEY": "", "GEMINI_API_KEY": "test-key"},
                clear=False,
            ),
            patch("src.agents.experts.video_generation.tool.genai.Client", return_value=fake_client),
        ):
            result = await video_tools.veo_video_generation_tool("draw a cat video")

        self.assertEqual(result["status"], "success")
        generate_videos_mock.assert_awaited_once()

    async def test_veo_tool_supports_video_extension_parameters(self) -> None:
        generate_videos_mock = AsyncMock(
            return_value=SimpleNamespace(
                done=True,
                result=SimpleNamespace(
                    generated_videos=[SimpleNamespace(video="video-file-id")]
                ),
            )
        )
        fake_client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_videos=generate_videos_mock),
                operations=SimpleNamespace(get=AsyncMock()),
                files=SimpleNamespace(download=AsyncMock(return_value=b"video-data")),
            )
        )

        with (
            patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}, clear=False),
            patch("src.agents.experts.video_generation.tool.genai.Client", return_value=fake_client),
            patch(
                "src.agents.experts.video_generation.tool._read_workspace_video_as_genai_video",
                return_value=video_tools.types.Video(video_bytes=b"video-bytes", mime_type="video/mp4"),
            ),
        ):
            result = await video_tools.veo_video_generation_tool(
                "continue the motion naturally",
                input_paths=["generated/session/source.mp4"],
                mode="video_extension",
                duration_seconds=8,
                negative_prompt="glitches",
                person_generation="allow_adult",
                seed=123,
                enhance_prompt=False,
            )

        self.assertEqual(result["status"], "success")
        kwargs = generate_videos_mock.await_args.kwargs
        self.assertEqual(kwargs["source"].video.mime_type, "video/mp4")
        self.assertEqual(kwargs["config"].duration_seconds, 8)
        self.assertEqual(kwargs["config"].negative_prompt, "glitches")
        self.assertEqual(kwargs["config"].person_generation, "allow_adult")
        self.assertEqual(kwargs["config"].seed, 123)
        self.assertFalse(kwargs["config"].enhance_prompt)

    async def test_veo_tool_supports_reference_style_images(self) -> None:
        generate_videos_mock = AsyncMock(
            return_value=SimpleNamespace(
                done=True,
                result=SimpleNamespace(
                    generated_videos=[SimpleNamespace(video="video-file-id")]
                ),
            )
        )
        fake_client = SimpleNamespace(
            aio=SimpleNamespace(
                models=SimpleNamespace(generate_videos=generate_videos_mock),
                operations=SimpleNamespace(get=AsyncMock()),
                files=SimpleNamespace(download=AsyncMock(return_value=b"video-data")),
            )
        )

        with (
            patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}, clear=False),
            patch("src.agents.experts.video_generation.tool.genai.Client", return_value=fake_client),
            patch(
                "src.agents.experts.video_generation.tool._read_workspace_image_as_genai_image",
                return_value=video_tools.types.Image(image_bytes=b"image-bytes", mime_type="image/png"),
            ),
        ):
            result = await video_tools.veo_video_generation_tool(
                "apply this style to the scene",
                input_paths=["generated/session/style.png"],
                mode="reference_style",
            )

        self.assertEqual(result["status"], "success")
        reference_images = generate_videos_mock.await_args.kwargs["config"].reference_images
        self.assertEqual(len(reference_images), 1)
        self.assertEqual(
            str(reference_images[0].reference_type).lower(),
            str(video_tools.types.VideoGenerationReferenceType.STYLE).lower(),
        )

    async def test_veo_tool_rejects_invalid_resolution_duration_combination(self) -> None:
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}, clear=False):
            result = await video_tools.veo_video_generation_tool(
                "draw a cat video",
                resolution="1080p",
                duration_seconds=4,
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("resolution=1080p requires duration_seconds=8", result["message"])

    async def test_veo_tool_rejects_invalid_video_extension_resolution(self) -> None:
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}, clear=False):
            result = await video_tools.veo_video_generation_tool(
                "continue the motion naturally",
                input_paths=["generated/session/source.mp4"],
                mode="video_extension",
                resolution="1080p",
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("mode=video_extension only supports resolution=720p", result["message"])


if __name__ == "__main__":
    unittest.main()
