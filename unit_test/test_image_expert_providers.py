import unittest
import base64
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.agents.experts.image_editing.image_editing_agent import ImageEditingAgent
from src.agents.experts.image_generation.image_generation_agent import ImageGenerationAgent
from src.agents.experts.image_editing import tool as editing_tools
from src.agents.experts.image_generation import tool as generation_tools
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


class ImageExpertProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_generation_uses_nano_banana_by_default(self) -> None:
        agent = ImageGenerationAgent(name="ImageGenerationAgent")
        ctx = _build_ctx({"current_parameters": {"prompt": "draw a cat"}, "step": 0})

        with (
            patch(
                "src.agents.experts.image_generation.image_generation_agent.generation_tools.prompt_enhancement_tool",
                new=AsyncMock(return_value={"status": "success", "message": "enhanced cat"}),
            ),
            patch(
                "src.agents.experts.image_generation.image_generation_agent.save_binary_output",
                return_value=workspace_root() / "generated" / "session_1" / "step1_generation_output0.png",
            ),
            patch(
                "src.agents.experts.image_generation.image_generation_agent.generation_tools.nano_banana_image_generation_tool",
                new=AsyncMock(
                    return_value={
                        "status": "success",
                        "message": b"png-data",
                        "provider": "gemini",
                        "model_name": "gemini-3.1-flash-image-preview",
                    }
                ),
            ) as nano_mock,
            patch(
                "src.agents.experts.image_generation.image_generation_agent.generation_tools.seedream_image_generation_tool",
                new=AsyncMock(),
            ) as seedream_mock,
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        nano_mock.assert_awaited_once()
        seedream_mock.assert_not_called()

    async def test_image_generation_uses_seedream_when_requested(self) -> None:
        agent = ImageGenerationAgent(name="ImageGenerationAgent")
        ctx = _build_ctx(
            {"current_parameters": {"prompt": "draw a cat", "provider": "seedream"}, "step": 0}
        )

        with (
            patch(
                "src.agents.experts.image_generation.image_generation_agent.generation_tools.prompt_enhancement_tool",
                new=AsyncMock(return_value={"status": "success", "message": "enhanced cat"}),
            ),
            patch(
                "src.agents.experts.image_generation.image_generation_agent.save_binary_output",
                return_value=workspace_root() / "generated" / "session_1" / "step1_generation_output0.png",
            ),
            patch(
                "src.agents.experts.image_generation.image_generation_agent.generation_tools.nano_banana_image_generation_tool",
                new=AsyncMock(),
            ) as nano_mock,
            patch(
                "src.agents.experts.image_generation.image_generation_agent.generation_tools.seedream_image_generation_tool",
                new=AsyncMock(
                    return_value={
                        "status": "success",
                        "message": b"png-data",
                        "provider": "seedream",
                        "model_name": "doubao-seedream-5-0-260128",
                    }
                ),
            ) as seedream_mock,
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        seedream_mock.assert_awaited_once_with("enhanced cat")
        nano_mock.assert_not_called()

    async def test_image_generation_uses_gpt_image_when_requested(self) -> None:
        agent = ImageGenerationAgent(name="ImageGenerationAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "prompt": "draw a cat",
                    "provider": "gpt_image",
                    "size": "1536x1024",
                    "quality": "medium",
                },
                "step": 0,
            }
        )

        with (
            patch(
                "src.agents.experts.image_generation.image_generation_agent.generation_tools.prompt_enhancement_tool",
                new=AsyncMock(return_value={"status": "success", "message": "enhanced cat"}),
            ),
            patch(
                "src.agents.experts.image_generation.image_generation_agent.save_binary_output",
                return_value=workspace_root() / "generated" / "session_1" / "step1_generation_output0.png",
            ),
            patch(
                "src.agents.experts.image_generation.image_generation_agent.generation_tools.nano_banana_image_generation_tool",
                new=AsyncMock(),
            ) as nano_mock,
            patch(
                "src.agents.experts.image_generation.image_generation_agent.generation_tools.seedream_image_generation_tool",
                new=AsyncMock(),
            ) as seedream_mock,
            patch(
                "src.agents.experts.image_generation.image_generation_agent.generation_tools.gpt_image_generation",
                new=AsyncMock(
                    return_value=generation_tools.ImageGenerationResult(
                        status="success",
                        message=b"png-data",
                        provider="gpt_image",
                        model_name="gpt-image-2",
                    )
                ),
            ) as gpt_image_mock,
            patch.object(
                generation_tools.API_CONFIG,
                "OPENAI_API_KEY",
                "test-key",
            ),
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        gpt_image_mock.assert_awaited_once_with(
            "enhanced cat",
            "test-key",
            size="1536x1024",
            quality="medium",
        )
        seedream_mock.assert_not_called()
        nano_mock.assert_not_called()

    async def test_image_generation_reports_output_artifact_name_in_message(self) -> None:
        agent = ImageGenerationAgent(name="ImageGenerationAgent")
        ctx = _build_ctx({"current_parameters": {"prompt": "draw a cat"}, "step": 0})

        with (
            patch(
                "src.agents.experts.image_generation.image_generation_agent.generation_tools.prompt_enhancement_tool",
                new=AsyncMock(return_value={"status": "success", "message": "enhanced cat"}),
            ),
            patch(
                "src.agents.experts.image_generation.image_generation_agent.save_binary_output",
                return_value=workspace_root() / "generated" / "session_1" / "step1_generation_output0.png",
            ),
            patch(
                "src.agents.experts.image_generation.image_generation_agent.generation_tools.nano_banana_image_generation_tool",
                new=AsyncMock(
                    return_value={
                        "status": "success",
                        "message": b"png-data",
                        "provider": "gemini",
                        "model_name": "gemini-3.1-flash-image-preview",
                    }
                ),
            ),
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertIn("step1_generation_output0.png", current_output["message"])
        self.assertEqual(current_output["output_files"][0]["path"], "generated/session_1/step1_generation_output0.png")

    async def test_image_editing_uses_nano_banana_by_default(self) -> None:
        agent = ImageEditingAgent(name="ImageEditingAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_paths": ["inbox/cli/session_1/a.png"],
                    "prompt": ["make it blue"],
                },
                "step": 0,
            }
        )

        with (
            patch(
                "src.agents.experts.image_editing.image_editing_agent.save_binary_output",
                return_value=workspace_root() / "generated" / "session_1" / "step1_editing_output0.png",
            ),
            patch(
                "src.agents.experts.image_editing.image_editing_agent.editing_tools.nano_banana_image_edit_tool",
                new=AsyncMock(return_value={"status": "success", "message": [b"png-data"]}),
            ) as nano_mock,
            patch(
                "src.agents.experts.image_editing.image_editing_agent.editing_tools.seedream_image_edit_tool",
                new=AsyncMock(),
            ) as seedream_mock,
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        nano_mock.assert_awaited_once()
        seedream_mock.assert_not_called()

    async def test_image_editing_uses_seedream_when_requested(self) -> None:
        agent = ImageEditingAgent(name="ImageEditingAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_paths": ["inbox/cli/session_1/a.png"],
                    "prompt": ["make it blue"],
                    "provider": "seedream",
                },
                "step": 0,
            }
        )

        with (
            patch(
                "src.agents.experts.image_editing.image_editing_agent.save_binary_output",
                return_value=workspace_root() / "generated" / "session_1" / "step1_editing_output0.png",
            ),
            patch(
                "src.agents.experts.image_editing.image_editing_agent.editing_tools.nano_banana_image_edit_tool",
                new=AsyncMock(),
            ) as nano_mock,
            patch(
                "src.agents.experts.image_editing.image_editing_agent.editing_tools.seedream_image_edit_tool",
                new=AsyncMock(return_value={"status": "success", "message": [b"png-data"]}),
            ) as seedream_mock,
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        seedream_mock.assert_awaited_once()
        nano_mock.assert_not_called()

    async def test_seedream_image_generation_works_without_legacy_types_module(self) -> None:
        image_payload = base64.b64encode(b"png-data").decode("utf-8")

        class _FakeArk:
            def __init__(self, **_kwargs) -> None:
                self.images = SimpleNamespace(
                    generate=lambda **_kwargs: SimpleNamespace(
                        error=None,
                        data=[SimpleNamespace(b64_json=image_payload)],
                    )
                )

        fake_sdk = SimpleNamespace(Ark=_FakeArk)
        with (
            patch.dict(sys.modules, {"volcenginesdkarkruntime": fake_sdk}, clear=False),
            patch.dict(os.environ, {"ARK_API_KEY": "test-key"}, clear=False),
        ):
            result = await generation_tools.seedream_image_generation("draw a cat", "test-key")

        self.assertEqual(result.status, "success")
        self.assertEqual(result.provider, "seedream")
        self.assertEqual(result.message, b"png-data")

    async def test_seedream_image_editing_works_without_legacy_types_module(self) -> None:
        image_payload = base64.b64encode(b"edited-png").decode("utf-8")

        class _FakeArk:
            def __init__(self, **_kwargs) -> None:
                self.images = SimpleNamespace(
                    generate=lambda **_kwargs: SimpleNamespace(
                        error=None,
                        data=[SimpleNamespace(b64_json=image_payload)],
                    )
                )

        fake_sdk = SimpleNamespace(Ark=_FakeArk)
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_path = Path(tmpdir) / "sample.png"
            sample_path.write_bytes(b"fake-image-bytes")
            tool_context = SimpleNamespace(
                state={"current_parameters": {"input_paths": ["inbox/cli/session_1/a.png"]}}
            )
            with (
                patch.dict(sys.modules, {"volcenginesdkarkruntime": fake_sdk}, clear=False),
                patch.dict(os.environ, {"ARK_API_KEY": "test-key"}, clear=False),
                patch(
                    "src.agents.experts.image_editing.tool.resolve_workspace_path",
                    return_value=sample_path,
                ),
            ):
                result = await editing_tools.seedream_image_edit_tool(tool_context, ["make it blue"])

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["provider"], "seedream")
        self.assertEqual(result["message"][0], b"edited-png")

    async def test_gpt_image_generation_returns_binary_payload(self) -> None:
        image_payload = base64.b64encode(b"gpt-image-png").decode("utf-8")
        generate_kwargs: dict[str, object] = {}

        class _FakeClient:
            def __init__(self, **_kwargs) -> None:
                self.images = SimpleNamespace(
                    generate=lambda **kwargs: (
                        generate_kwargs.update(kwargs)
                        or SimpleNamespace(
                        data=[SimpleNamespace(b64_json=image_payload)],
                        )
                    )
                )

        with patch("src.agents.experts.image_generation.tool.OpenAI", _FakeClient):
            result = await generation_tools.gpt_image_generation("draw a cat", "test-key")

        self.assertEqual(result.status, "success")
        self.assertEqual(result.provider, "gpt_image")
        self.assertEqual(result.model_name, "gpt-image-2")
        self.assertEqual(result.message, b"gpt-image-png")
        self.assertEqual(generate_kwargs["model"], "gpt-image-2")


if __name__ == "__main__":
    unittest.main()
