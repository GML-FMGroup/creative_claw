import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.agents.experts.dino_xseek.image_ground_agent import ImageGroundAgent


def _build_ctx(state: dict) -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(
            state=state,
            app_name="test_app",
            user_id="user_1",
            id="session_1",
        ),
    )


class ImageGroundAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_returns_structured_bbox_results(self) -> None:
        agent = ImageGroundAgent(name="ImageGroundAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_path": "inbox/session/a.png",
                    "prompt": "Sun Wukong",
                }
            }
        )

        with patch(
            "src.agents.experts.dino_xseek.image_ground_agent.dino_xseek_detection_tool",
            new=AsyncMock(
                return_value={
                    "status": "success",
                    "message": "Detected 2 object(s) for prompt 'Sun Wukong'.",
                    "input_path": "inbox/session/a.png",
                    "prompt": "Sun Wukong",
                    "objects": [
                        {"bbox": [10.0, 20.0, 30.0, 40.0]},
                        {"bbox": [50.0, 60.0, 70.0, 80.0]},
                    ],
                    "bboxes": [
                        [10.0, 20.0, 30.0, 40.0],
                        [50.0, 60.0, 70.0, 80.0],
                    ],
                    "task_uuid": "task-123",
                    "session_id": "session-456",
                    "provider": "deepdataspace",
                    "model_name": "DINO-XSeek-1.0",
                }
            ),
        ) as tool_mock:
            events = [event async for event in agent._run_async_impl(ctx)]

        tool_mock.assert_awaited_once_with(ctx, "inbox/session/a.png", "Sun Wukong")
        self.assertEqual(len(events), 1)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "success")
        self.assertEqual(current_output["results"][0]["bboxes"][1], [50.0, 60.0, 70.0, 80.0])
        self.assertEqual(events[0].actions.state_delta["image_ground_results"][0]["task_uuid"], "task-123")

    async def test_agent_rejects_missing_required_parameters(self) -> None:
        agent = ImageGroundAgent(name="ImageGroundAgent")
        ctx = _build_ctx({"current_parameters": {"input_path": "inbox/session/a.png"}})

        events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("must include: input_path, prompt", current_output["message"])


if __name__ == "__main__":
    unittest.main()
