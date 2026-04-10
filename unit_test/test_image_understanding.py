import tempfile
import unittest
from typing import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from google.genai.types import Content, Part

from src.agents.experts.image_understanding import tool as understanding_tool
from src.agents.experts.image_understanding.image_understanding_agent import ImageUnderstandingAgent
from src.runtime.workspace import workspace_relative_path, workspace_root


def _build_ctx(state: dict) -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(
            state=state,
            app_name="test_app",
            user_id="user_1",
            id="session_1",
        ),
    )


class ImageUnderstandingTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_passes_individual_modes_to_tool(self) -> None:
        agent = ImageUnderstandingAgent(name="ImageUnderstandingAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_paths": ["inbox/session/a.png", "inbox/session/b.png"],
                    "mode": ["style", "ocr"],
                }
            }
        )

        with (
            patch(
                "src.agents.experts.image_understanding.image_understanding_agent.image_to_text_tool",
                new=AsyncMock(
                    side_effect=[
                        {
                            "status": "success",
                            "message": "style-result",
                            "analysis_text": "style-result",
                            "basic_info": "info-a",
                            "provider": "google_adk",
                            "model_name": "gemini-test",
                        },
                        {
                            "status": "success",
                            "message": "ocr-result",
                            "analysis_text": "ocr-result",
                            "basic_info": "info-b",
                            "provider": "google_adk",
                            "model_name": "gemini-test",
                        },
                    ]
                ),
            ) as tool_mock,
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(tool_mock.await_args_list[0].args[1:], ("inbox/session/a.png", "style"))
        self.assertEqual(tool_mock.await_args_list[1].args[1:], ("inbox/session/b.png", "ocr"))
        current_output = events[0].actions.state_delta["current_output"]
        self.assertIn("image inbox/session/a.png style: style-result", current_output["output_text"])
        self.assertIn("image inbox/session/b.png ocr: ocr-result", current_output["output_text"])
        self.assertEqual(current_output["results"][0]["mode"], "style")
        self.assertEqual(events[0].actions.state_delta["image_understanding_results"][1]["basic_info"], "info-b")

    async def test_agent_reuses_single_mode_for_multiple_images(self) -> None:
        agent = ImageUnderstandingAgent(name="ImageUnderstandingAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_paths": ["inbox/session/a.png", "inbox/session/b.png"],
                    "mode": "description",
                }
            }
        )

        with (
            patch(
                "src.agents.experts.image_understanding.image_understanding_agent.image_to_text_tool",
                new=AsyncMock(
                    side_effect=[
                        {"status": "success", "message": "desc-a"},
                        {"status": "success", "message": "desc-b"},
                    ]
                ),
            ) as tool_mock,
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(tool_mock.await_args_list[0].args[1:], ("inbox/session/a.png", "description"))
        self.assertEqual(tool_mock.await_args_list[1].args[1:], ("inbox/session/b.png", "description"))
        self.assertEqual(events[0].actions.state_delta["current_output"]["status"], "success")

    async def test_agent_rejects_mismatched_mode_list_length(self) -> None:
        agent = ImageUnderstandingAgent(name="ImageUnderstandingAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_paths": ["inbox/session/a.png", "inbox/session/b.png"],
                    "mode": ["description", "style", "ocr"],
                }
            }
        )

        events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("must contain exactly one value or match the number of input images", current_output["message"])

    async def test_tool_supports_all_mode_and_appends_basic_info(self) -> None:
        captured_llm_request: dict[str, object] = {}

        class _FakeEvent:
            def __init__(self, text: str) -> None:
                self.content = Content(role="model", parts=[Part(text=text)])

            def is_final_response(self) -> bool:
                return True

        class _FakeLlmAgent:
            def __init__(self, **kwargs) -> None:
                self.before_model_callback = kwargs["before_model_callback"]
                captured_llm_request["model"] = kwargs["model"]
                captured_llm_request["instruction"] = kwargs["instruction"]

            async def run_async(self, ctx) -> AsyncGenerator[_FakeEvent, None]:
                llm_request = SimpleNamespace(contents=[])
                self.before_model_callback(SimpleNamespace(state={}), llm_request)
                captured_llm_request["contents"] = llm_request.contents
                yield _FakeEvent("analysis result")

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmp_dir:
            image_path = Path(tmp_dir) / "sample.png"
            Image.new("RGBA", (4, 3), color=(255, 0, 0, 255)).save(image_path)
            relative_path = workspace_relative_path(image_path)

            with (
                patch("src.agents.experts.image_understanding.tool.LlmAgent", _FakeLlmAgent),
            ):
                result = await understanding_tool.image_to_text_tool(_build_ctx({}), relative_path, mode="all")

        self.assertEqual(result["status"], "success")
        self.assertIn("analysis result", result["message"])
        self.assertIn("Basic image info: format=PNG, size=4x3, mode=RGBA", result["message"])
        self.assertEqual(result["provider"], "google_adk")
        self.assertEqual(result["model_name"], understanding_tool.SYS_CONFIG.llm_model)
        user_prompt = captured_llm_request["contents"][0].parts[0].text
        self.assertIn("Finally, extract all readable text from the image", user_prompt)

    async def test_agent_persists_structured_results_on_success(self) -> None:
        agent = ImageUnderstandingAgent(name="ImageUnderstandingAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_path": "inbox/session/a.png",
                    "mode": "all",
                }
            }
        )

        with patch(
            "src.agents.experts.image_understanding.image_understanding_agent.image_to_text_tool",
            new=AsyncMock(
                return_value={
                    "status": "success",
                    "message": "combined result",
                    "analysis_text": "analysis body",
                    "basic_info": "Basic image info: ...",
                    "input_path": "inbox/session/a.png",
                    "mode": "all",
                    "provider": "google_adk",
                    "model_name": "gemini-test",
                }
            ),
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        state_delta = events[0].actions.state_delta
        current_output = state_delta["current_output"]
        self.assertEqual(current_output["results"][0]["analysis_text"], "analysis body")
        self.assertEqual(current_output["results"][0]["provider"], "google_adk")
        self.assertEqual(state_delta["image_understanding_results"][0]["mode"], "all")
        self.assertEqual(current_output["message_for_user"], "Finished understanding 1 images with 1 successful analyses.")


if __name__ == "__main__":
    unittest.main()
