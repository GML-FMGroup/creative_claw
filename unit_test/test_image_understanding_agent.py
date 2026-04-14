import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.agents.experts.image_understanding.image_understanding_agent import ImageUnderstandingAgent


def _build_ctx(state: dict) -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(
            state=state,
            app_name="test_app",
            user_id="user_1",
            id="session_1",
        ),
    )


class ImageUnderstandingAgentValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_accepts_prompt_mode(self) -> None:
        agent = ImageUnderstandingAgent(name="ImageUnderstandingAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_path": "inbox/session/a.png",
                    "mode": "prompt",
                }
            }
        )

        with patch(
            "src.agents.experts.image_understanding.image_understanding_agent.image_to_text_tool",
            new=AsyncMock(return_value={"status": "success", "message": "ok"}),
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(events[0].actions.state_delta["current_output"]["status"], "success")

    async def test_agent_rejects_missing_image_inputs(self) -> None:
        agent = ImageUnderstandingAgent(name="ImageUnderstandingAgent")
        ctx = _build_ctx({"current_parameters": {"mode": "description"}})

        events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("must include: input_path or input_paths, mode", current_output["message"])

    async def test_agent_rejects_invalid_mode_values(self) -> None:
        agent = ImageUnderstandingAgent(name="ImageUnderstandingAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_path": "inbox/session/a.png",
                    "mode": "nonsense",
                }
            }
        )

        events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("Supported modes are", current_output["message"])


if __name__ == "__main__":
    unittest.main()
