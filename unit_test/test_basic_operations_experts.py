import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.agents.experts.audio_basic_operations.audio_basic_operations_agent import (
    AudioBasicOperationsAgent,
)
from src.agents.experts.image_basic_operations.image_basic_operations_agent import (
    ImageBasicOperationsAgent,
)
from src.agents.experts.video_basic_operations.video_basic_operations_agent import (
    VideoBasicOperationsAgent,
)
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


class BasicOperationsExpertTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_basic_operations_info_returns_structured_output(self) -> None:
        agent = ImageBasicOperationsAgent(name="ImageBasicOperations")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "operation": "info",
                    "input_path": "inbox/cli/sample.png",
                }
            }
        )

        with patch(
            "src.agents.experts.image_basic_operations.tool.BuiltinToolbox"
        ) as toolbox_cls:
            toolbox_cls.return_value.image_info.return_value = (
                '{"path":"inbox/cli/sample.png","format":"PNG","width":100,"height":80,"mode":"RGB","exif":{}}'
            )
            events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "success")
        self.assertIn('"format":"PNG"', current_output["output_text"])
        self.assertEqual(
            events[0].actions.state_delta["image_basic_operation_results"]["width"],
            100,
        )

    async def test_video_basic_operations_concat_returns_output_file(self) -> None:
        agent = VideoBasicOperationsAgent(name="VideoBasicOperations")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "operation": "concat",
                    "input_paths": ["generated/a.mp4", "generated/b.mp4"],
                }
            }
        )

        with patch(
            "src.agents.experts.video_basic_operations.tool.BuiltinToolbox"
        ) as toolbox_cls, patch(
            "src.agents.experts.basic_operations_helpers.relocate_generated_output"
        ) as relocate_mock:
            toolbox_cls.return_value.video_concat.return_value = "generated/a_concat.mp4"
            relocate_mock.return_value = (
                workspace_root()
                / "generated"
                / "session_1"
                / "turn_0"
                / "turn0_step0_videobasicoperations_concat_output0.mp4"
            )
            events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "success")
        self.assertEqual(
            current_output["output_files"][0]["path"],
            "generated/session_1/turn_0/turn0_step0_videobasicoperations_concat_output0.mp4",
        )
        self.assertEqual(
            events[0].actions.state_delta["video_basic_operation_results"]["operation"],
            "concat",
        )

    async def test_audio_basic_operations_propagates_tool_errors(self) -> None:
        agent = AudioBasicOperationsAgent(name="AudioBasicOperations")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "operation": "convert",
                    "input_path": "generated/sample.wav",
                    "output_format": "mp3",
                }
            }
        )

        with patch(
            "src.agents.experts.audio_basic_operations.tool.BuiltinToolbox"
        ) as toolbox_cls:
            toolbox_cls.return_value.audio_convert.return_value = "Error converting audio: boom"
            events = [event async for event in agent._run_async_impl(ctx)]

        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("Error converting audio: boom", current_output["message"])
