import unittest
import os
import requests
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

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
                        "model_name": "doubao-seedance-2-0-260128",
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
        seedance_mock.assert_awaited_once_with(
            "enhanced cat video",
            input_paths=[],
            mode="prompt",
            aspect_ratio="16:9",
            model_name="doubao-seedance-2-0-260128",
            resolution="720p",
            duration_seconds=5,
            generate_audio=None,
            watermark=False,
            seed=None,
        )
        veo_mock.assert_not_called()

    async def test_video_generation_passes_seedance_2_fast_audio_parameters(self) -> None:
        agent = VideoGenerationAgent(name="VideoGenerationAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "prompt": "两只猫对话。猫A说：“你妈妈一个月赚多少钱？”猫B说：“两万五。”",
                    "provider": "seedance",
                    "model_name": "doubao-seedance-2-0-fast-260128",
                    "prompt_rewrite": "off",
                    "aspect_ratio": "9:16",
                    "resolution": "720p",
                    "duration_seconds": 8,
                    "generate_audio": True,
                    "watermark": False,
                    "seed": -1,
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
                "src.agents.experts.video_generation.video_generation_agent.video_tools.seedance_video_generation_tool",
                new=AsyncMock(
                    return_value={
                        "status": "success",
                        "message": b"video-data",
                        "provider": "seedance",
                        "model_name": "doubao-seedance-2-0-fast-260128",
                    }
                ),
            ) as seedance_mock,
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        enhancement_mock.assert_not_called()
        seedance_mock.assert_awaited_once_with(
            "两只猫对话。猫A说：“你妈妈一个月赚多少钱？”猫B说：“两万五。”",
            input_paths=[],
            mode="prompt",
            aspect_ratio="9:16",
            model_name="doubao-seedance-2-0-fast-260128",
            resolution="720p",
            duration_seconds=8,
            generate_audio=True,
            watermark=False,
            seed=-1,
        )

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
        )
        seedance_mock.assert_not_called()

    async def test_video_generation_skips_local_prompt_enhancement_when_prompt_rewrite_is_off(self) -> None:
        agent = VideoGenerationAgent(name="VideoGenerationAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "prompt": "draw a cat video",
                    "provider": "veo",
                    "prompt_rewrite": "off",
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
        )

    async def test_video_generation_rejects_invalid_prompt_rewrite_value(self) -> None:
        agent = VideoGenerationAgent(name="VideoGenerationAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "prompt": "draw a cat video",
                    "prompt_rewrite": "sometimes",
                },
                "step": 0,
            }
        )

        events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("prompt_rewrite must be one of", current_output["message"])

    async def test_video_generation_uses_kling_when_requested(self) -> None:
        agent = VideoGenerationAgent(name="VideoGenerationAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "provider": "kling",
                    "mode": "multi_reference",
                    "input_paths": ["generated/a.png", "generated/b.png"],
                    "prompt": "keep the subject consistent",
                    "duration_seconds": 10,
                    "kling_mode": "pro",
                    "model_name": "kling-v1-6",
                },
                "step": 0,
            }
        )

        with (
            patch(
                "src.agents.experts.video_generation.video_generation_agent.video_tools.prompt_enhancement_tool",
                new=AsyncMock(return_value={"status": "success", "message": "enhanced kling prompt"}),
            ),
            patch(
                "src.agents.experts.video_generation.video_generation_agent.save_binary_output",
                return_value=workspace_root() / "generated" / "session_1" / "step1_video_generation_output0.mp4",
            ),
            patch(
                "src.agents.experts.video_generation.video_generation_agent.video_tools.kling_video_generation_tool",
                new=AsyncMock(
                    return_value={
                        "status": "success",
                        "message": b"video-data",
                        "provider": "kling",
                        "model_name": "kling-v1-6",
                    }
                ),
            ) as kling_mock,
            patch(
                "src.agents.experts.video_generation.video_generation_agent.video_tools.seedance_video_generation_tool",
                new=AsyncMock(),
            ) as seedance_mock,
            patch(
                "src.agents.experts.video_generation.video_generation_agent.video_tools.veo_video_generation_tool",
                new=AsyncMock(),
            ) as veo_mock,
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        kling_mock.assert_awaited_once_with(
            "enhanced kling prompt",
            input_paths=["generated/a.png", "generated/b.png"],
            mode="multi_reference",
            aspect_ratio="16:9",
            duration_seconds=10,
            negative_prompt="",
            model_name="kling-v1-6",
            kling_mode="pro",
        )
        seedance_mock.assert_not_called()
        veo_mock.assert_not_called()

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
                        "model_name": "doubao-seedance-2-0-260128",
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
    def setUp(self) -> None:
        video_tools._resolved_kling_api_base = None

    def tearDown(self) -> None:
        video_tools._resolved_kling_api_base = None

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
                "model": "doubao-seedance-2-0-260128",
                "content": [{"type": "text", "text": "draw a cat video"}],
                "ratio": "9:16",
                "resolution": "720p",
                "duration": 5,
                "watermark": False,
                "generate_audio": True,
            },
        )

    async def test_seedance_tool_accepts_more_than_three_reference_images(self) -> None:
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

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            image_paths = []
            for index in range(4):
                image_path = os.path.join(tmpdir, f"reference_{index}.png")
                Image.new("RGB", (64, 64), color=(index * 20, 40, 80)).save(image_path)
                image_paths.append(image_path)

            with (
                patch.dict(os.environ, {"ARK_API_KEY": "test-key"}, clear=False),
                patch.dict(sys.modules, {"volcenginesdkarkruntime": fake_module}),
            ):
                result = await video_tools.seedance_video_generation_tool(
                    "keep the product identity consistent",
                    input_paths=image_paths,
                    mode="reference_asset",
                    duration_seconds=8,
                )

        self.assertEqual(result["status"], "error")
        create_mock.assert_called_once()
        content = create_mock.call_args.kwargs["content"]
        reference_items = [item for item in content if item.get("role") == "reference_image"]
        self.assertEqual(len(reference_items), 4)

    async def test_seedance_tool_passes_2_fast_audio_parameters(self) -> None:
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
                "两只猫对话。猫A说：“你好。”猫B说：“你好呀。”",
                aspect_ratio="9:16",
                model_name="doubao-seedance-2-0-fast-260128",
                resolution="720p",
                duration_seconds=8,
                generate_audio=True,
                watermark=False,
                seed=-1,
            )

        self.assertEqual(result["status"], "error")
        create_mock.assert_called_once()
        self.assertEqual(
            create_mock.call_args.kwargs,
            {
                "model": "doubao-seedance-2-0-fast-260128",
                "content": [{"type": "text", "text": "两只猫对话。猫A说：“你好。”猫B说：“你好呀。”"}],
                "ratio": "9:16",
                "resolution": "720p",
                "duration": 8,
                "watermark": False,
                "generate_audio": True,
                "seed": -1,
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
            )

        self.assertEqual(result["status"], "success")
        kwargs = generate_videos_mock.await_args.kwargs
        self.assertEqual(kwargs["source"].video.mime_type, "video/mp4")
        self.assertEqual(kwargs["config"].duration_seconds, 8)
        self.assertEqual(kwargs["config"].negative_prompt, "glitches")
        self.assertEqual(kwargs["config"].person_generation, "allow_adult")
        self.assertEqual(kwargs["config"].seed, 123)
        self.assertNotIn("enhance_prompt", kwargs["config"].model_dump(exclude_none=True))

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

    async def test_kling_tool_supports_multi_reference_images(self) -> None:
        submit_mock = MagicMock(return_value={"code": 0, "data": {"task_id": "task-1"}})
        poll_mock = MagicMock(
            return_value={
                "code": 0,
                "data": {
                    "task_status": "succeed",
                    "task_result": {"videos": [{"url": "https://example.com/video.mp4"}]},
                },
            }
        )
        download_mock = MagicMock(return_value=b"video-data")

        with (
            patch.dict(
                os.environ,
                {
                    "KLING_ACCESS_KEY": "test-access",
                    "KLING_SECRET_KEY": "test-secret",
                    "KLING_API_BASE": "https://api-beijing.klingai.com",
                },
                clear=False,
            ),
            patch(
                "src.agents.experts.video_generation.tool._submit_kling_task_sync",
                submit_mock,
            ),
            patch(
                "src.agents.experts.video_generation.tool._get_kling_task_sync",
                poll_mock,
            ),
            patch(
                "src.agents.experts.video_generation.tool._download_binary_sync",
                download_mock,
            ),
            patch(
                "src.agents.experts.video_generation.tool._validate_kling_input_images",
            ),
            patch(
                "src.agents.experts.video_generation.tool._read_workspace_file_as_base64",
                side_effect=["base64-a", "base64-b"],
            ),
        ):
            result = await video_tools.kling_video_generation_tool(
                "keep the subject consistent",
                input_paths=["generated/a.png", "generated/b.png"],
                mode="multi_reference",
                duration_seconds=10,
                kling_mode="pro",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["model_name"], "kling-v1-6")
        submit_kwargs = submit_mock.call_args.kwargs
        self.assertEqual(submit_kwargs["endpoint"], "/v1/videos/multi-image2video")
        self.assertEqual(
            submit_kwargs["payload"]["image_list"],
            [{"image": "base64-a"}, {"image": "base64-b"}],
        )
        self.assertEqual(submit_kwargs["payload"]["mode"], "pro")
        self.assertEqual(submit_kwargs["payload"]["duration"], "10")
        poll_mock.assert_called_once_with(
            api_base="https://api-beijing.klingai.com",
            endpoint="/v1/videos/multi-image2video/task-1",
            headers=submit_kwargs["headers"],
        )
        download_mock.assert_called_once_with("https://example.com/video.mp4")

    async def test_kling_tool_uses_v3_default_for_basic_prompt_route(self) -> None:
        submit_mock = MagicMock(return_value={"code": 0, "data": {"task_id": "task-1"}})
        poll_mock = MagicMock(
            return_value={
                "code": 0,
                "data": {
                    "task_status": "succeed",
                    "task_result": {"videos": [{"url": "https://example.com/video.mp4"}]},
                },
            }
        )
        download_mock = MagicMock(return_value=b"video-data")

        with (
            patch.dict(
                os.environ,
                {
                    "KLING_ACCESS_KEY": "test-access",
                    "KLING_SECRET_KEY": "test-secret",
                    "KLING_API_BASE": "https://api-beijing.klingai.com",
                },
                clear=False,
            ),
            patch(
                "src.agents.experts.video_generation.tool._submit_kling_task_sync",
                submit_mock,
            ),
            patch(
                "src.agents.experts.video_generation.tool._get_kling_task_sync",
                poll_mock,
            ),
            patch(
                "src.agents.experts.video_generation.tool._download_binary_sync",
                download_mock,
            ),
        ):
            result = await video_tools.kling_video_generation_tool("draw a cat video")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["model_name"], "kling-v3")
        self.assertEqual(
            submit_mock.call_args.kwargs["payload"]["model_name"],
            "kling-v3",
        )
        self.assertEqual(
            submit_mock.call_args.kwargs["endpoint"],
            "/v1/videos/text2video",
        )

    def test_kling_http_session_disables_environment_trust(self) -> None:
        session = video_tools._build_kling_http_session()
        try:
            self.assertFalse(session.trust_env)
        finally:
            session.close()

    def test_kling_api_base_probe_prefers_first_working_candidate_and_caches_it(self) -> None:
        with patch(
            "src.agents.experts.video_generation.tool._probe_kling_api_base_sync",
            side_effect=[False, True],
        ) as probe_mock:
            resolved_base = video_tools._resolve_kling_api_base("test-access", "test-secret")
            resolved_base_again = video_tools._resolve_kling_api_base("test-access", "test-secret")

        self.assertEqual(resolved_base, "https://api-singapore.klingai.com")
        self.assertEqual(resolved_base_again, "https://api-singapore.klingai.com")
        self.assertEqual(probe_mock.call_count, 2)

    def test_kling_api_base_probe_skips_when_user_configures_explicit_base(self) -> None:
        with patch(
            "src.agents.experts.video_generation.tool._probe_kling_api_base_sync",
        ) as probe_mock:
            resolved_base = video_tools._resolve_kling_api_base(
                "test-access",
                "test-secret",
                "https://api-beijing.klingai.com/",
            )

        self.assertEqual(resolved_base, "https://api-beijing.klingai.com")
        probe_mock.assert_not_called()

    def test_kling_task_query_retries_transient_ssl_error(self) -> None:
        session = MagicMock()
        response = MagicMock()
        response.json.return_value = {"code": 0, "data": {"task_status": "processing"}}
        session.get.side_effect = [
            requests.exceptions.SSLError("unexpected eof"),
            response,
        ]
        session.__enter__.return_value = session
        session.__exit__.return_value = None

        with (
            patch(
                "src.agents.experts.video_generation.tool._build_kling_http_session",
                return_value=session,
            ),
            patch("src.agents.experts.video_generation.tool.time.sleep"),
        ):
            payload = video_tools._get_kling_task_sync(
                api_base="https://api-beijing.klingai.com",
                endpoint="/v1/videos/image2video/task-1",
                headers={"Authorization": "Bearer token"},
            )

        self.assertEqual(payload["data"]["task_status"], "processing")
        self.assertEqual(session.get.call_count, 2)

    def test_kling_binary_download_retries_transient_ssl_error(self) -> None:
        session = MagicMock()
        response = MagicMock()
        response.content = b"video-data"
        session.get.side_effect = [
            requests.exceptions.SSLError("unexpected eof"),
            response,
        ]
        session.__enter__.return_value = session
        session.__exit__.return_value = None

        with (
            patch(
                "src.agents.experts.video_generation.tool._build_kling_http_session",
                return_value=session,
            ),
            patch("src.agents.experts.video_generation.tool.time.sleep"),
        ):
            payload = video_tools._download_binary_sync("https://example.com/video.mp4")

        self.assertEqual(payload, b"video-data")
        self.assertEqual(session.get.call_count, 2)

    def test_kling_image_validation_rejects_small_inputs_without_auto_resize(self) -> None:
        relative_path = "generated/unit_test/kling_small.png"
        image_path = workspace_root() / relative_path
        image_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (200, 200), color="white").save(image_path)

        try:
            with self.assertRaisesRegex(ValueError, "does not auto-resize"):
                video_tools._validate_kling_input_images(
                    mode="first_frame",
                    input_paths=[relative_path],
                )
        finally:
            image_path.unlink(missing_ok=True)

    async def test_kling_multi_reference_rejects_unsupported_model_name(self) -> None:
        with patch.dict(
            os.environ,
            {
                "KLING_ACCESS_KEY": "test-access",
                "KLING_SECRET_KEY": "test-secret",
            },
            clear=False,
        ):
            result = await video_tools.kling_video_generation_tool(
                "keep the subject consistent",
                input_paths=["generated/a.png", "generated/b.png"],
                mode="multi_reference",
                model_name="kling-v2-6",
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("supports only model_name=kling-v1-6", result["message"])

    async def test_kling_tool_rejects_unsupported_video_extension_mode(self) -> None:
        with patch.dict(
            os.environ,
            {
                "KLING_ACCESS_KEY": "test-access",
                "KLING_SECRET_KEY": "test-secret",
            },
            clear=False,
        ):
            result = await video_tools.kling_video_generation_tool(
                "continue the motion naturally",
                input_paths=["generated/session/source.mp4"],
                mode="video_extension",
            )

        self.assertEqual(result["status"], "error")
        self.assertIn("supports only mode=prompt", result["message"])


if __name__ == "__main__":
    unittest.main()
