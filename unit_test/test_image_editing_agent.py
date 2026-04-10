import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.agents.experts.image_editing.image_editing_agent import ImageEditingAgent
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


class ImageEditingAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_rejects_missing_required_parameters(self) -> None:
        agent = ImageEditingAgent(name="ImageEditingAgent")
        ctx = _build_ctx({"current_parameters": {"input_path": "inbox/session/a.png"}, "step": 0})

        events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("Required fields: input_path or input_paths, prompt", current_output["message"])

    async def test_agent_skips_empty_partial_results_and_keeps_successful_outputs(self) -> None:
        agent = ImageEditingAgent(name="ImageEditingAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_paths": ["inbox/session/a.png"],
                    "prompt": ["make it blue", "make it red"],
                },
                "step": 0,
            }
        )

        with (
            patch(
                "src.agents.experts.image_editing.image_editing_agent.editing_tools.nano_banana_image_edit_tool",
                new=AsyncMock(return_value={"status": "success", "message": [b"png-data", None]}),
            ),
            patch(
                "src.agents.experts.image_editing.image_editing_agent.save_binary_output",
                return_value=workspace_root() / "generated" / "session_1" / "step1_editing_output0.png",
            ) as save_mock,
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        save_mock.assert_called_once()
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "success")
        self.assertEqual(len(current_output["output_files"]), 1)
        self.assertEqual(current_output["output_files"][0]["path"], "generated/session_1/step1_editing_output0.png")
        self.assertIn("Image 1 editing succeeded", current_output["message"])
        self.assertIn("Image 2 editing failed. Prompt: make it red.", current_output["message"])

    async def test_agent_returns_error_when_all_edit_results_are_empty(self) -> None:
        agent = ImageEditingAgent(name="ImageEditingAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_paths": ["inbox/session/a.png"],
                    "prompt": ["make it blue"],
                },
                "step": 1,
            }
        )

        with patch(
            "src.agents.experts.image_editing.image_editing_agent.editing_tools.nano_banana_image_edit_tool",
            new=AsyncMock(return_value={"status": "success", "message": [None]}),
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("all edited images were empty", current_output["message"])


if __name__ == "__main__":
    unittest.main()
