import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.genai.types import Blob, Part

from src.agents.experts.image_editing.image_editing_agent import ImageEditingAgent
from src.agents.experts.image_generation.image_generation_agent import ImageGenerationAgent


class _FakeArtifactService:
    def __init__(self) -> None:
        self.saved: list[dict] = []

    async def save_artifact(self, **kwargs) -> None:
        self.saved.append(kwargs)


def _build_ctx(state: dict) -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(
            state=state,
            app_name="test_app",
            user_id="user_1",
            id="session_1",
        ),
        artifact_service=_FakeArtifactService(),
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
                        "model_name": "doubao-seedream-4-0-250828",
                    }
                ),
            ) as seedream_mock,
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        seedream_mock.assert_awaited_once_with("enhanced cat")
        nano_mock.assert_not_called()

    async def test_image_editing_uses_nano_banana_by_default(self) -> None:
        agent = ImageEditingAgent(name="ImageEditingAgent")
        ctx = _build_ctx(
            {"current_parameters": {"input_name": ["a.png"], "prompt": ["make it blue"]}, "step": 0}
        )

        with (
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
                    "input_name": ["a.png"],
                    "prompt": ["make it blue"],
                    "provider": "seedream",
                },
                "step": 0,
            }
        )

        with (
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


if __name__ == "__main__":
    unittest.main()
