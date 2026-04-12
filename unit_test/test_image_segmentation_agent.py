import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.agents.experts.image_segmentation.image_segmentation_agent import ImageSegmentationAgent


def _build_ctx(state: dict) -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(
            state=state,
            app_name="test_app",
            user_id="user_1",
            id="session_1",
        ),
    )


class ImageSegmentationAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_returns_mask_file_and_structured_results(self) -> None:
        agent = ImageSegmentationAgent(name="ImageSegmentationAgent")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "input_path": "inbox/session/a.png",
                    "prompt": "person",
                    "threshold": 0.3,
                }
            }
        )

        with patch(
            "src.agents.experts.image_segmentation.image_segmentation_agent.image_segmentation_tool",
            new=AsyncMock(
                return_value={
                    "status": "success",
                    "message": "Segmented 1 object(s) for prompt 'person'.",
                    "input_path": "inbox/session/a.png",
                    "prompt": "person",
                    "objects": [{"bbox": [1.0, 2.0, 3.0, 4.0], "score": 0.99}],
                    "bboxes": [[1.0, 2.0, 3.0, 4.0]],
                    "task_uuid": "task-123",
                    "session_id": "session-456",
                    "provider": "deepdataspace",
                    "model_name": "DINO-X-1.0",
                    "threshold": 0.3,
                    "mask_path": "generated/session_1/step1_segmentation_mask_output0.png",
                }
            ),
        ) as tool_mock, patch(
            "src.agents.experts.image_segmentation.image_segmentation_agent.resolve_workspace_path",
            side_effect=lambda value: f"/virtual/{value}",
        ), patch(
            "src.agents.experts.image_segmentation.image_segmentation_agent.build_workspace_file_record",
            return_value={
                "name": "step1_segmentation_mask_output0.png",
                "path": "generated/session_1/step1_segmentation_mask_output0.png",
                "description": "mask",
                "source": "expert",
            },
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        tool_mock.assert_awaited_once_with(
            ctx,
            "inbox/session/a.png",
            "person",
            model="DINO-X-1.0",
            threshold=0.3,
        )
        self.assertEqual(len(events), 1)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "success")
        self.assertEqual(current_output["output_files"][0]["source"], "expert")
        self.assertEqual(
            events[0].actions.state_delta["image_segmentation_results"][0]["mask_path"],
            "generated/session_1/step1_segmentation_mask_output0.png",
        )

    async def test_agent_rejects_missing_required_parameters(self) -> None:
        agent = ImageSegmentationAgent(name="ImageSegmentationAgent")
        ctx = _build_ctx({"current_parameters": {"input_path": "inbox/session/a.png"}})

        events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("must include: input_path, prompt", current_output["message"])
