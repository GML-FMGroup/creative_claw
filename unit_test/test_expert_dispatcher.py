from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from google.adk.agents import BaseAgent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions
from google.adk.sessions.state import State

from src.runtime.expert_registry import build_expert_contract_summary
from src.runtime.expert_dispatcher import (
    dispatch_expert_call,
    normalize_invoke_agent_parameters,
)


class _FakeExpertAgent(BaseAgent):
    def __init__(self, name: str) -> None:
        super().__init__(name=name, description="fake expert")

    async def _run_async_impl(self, ctx):
        yield Event(
            author=self.name,
            actions=EventActions(
                state_delta={
                    "step": 99,
                    "summary_history": ["child-summary"],
                    "current_output": {
                        "status": "success",
                        "message": "expert finished",
                        "output_text": "expert answer",
                    },
                    "app:shared_setting": "from-child",
                    "custom_key": "custom-value",
                }
            ),
        )


class _ParentStateInspectingExpertAgent(BaseAgent):
    def __init__(self, name: str) -> None:
        super().__init__(name=name, description="state-inspecting expert")

    async def _run_async_impl(self, ctx):
        saw_internal_key = "_adk_hidden" in ctx.session.state
        saw_user_prompt = "user_prompt" in ctx.session.state
        message = (
            "child saw filtered state"
            if not saw_internal_key and saw_user_prompt
            else "child saw unfiltered state"
        )
        yield Event(
            author=self.name,
            actions=EventActions(
                state_delta={
                    "current_output": {
                        "status": "success",
                        "message": message,
                    }
                }
            ),
        )


class ExpertDispatcherTests(unittest.IsolatedAsyncioTestCase):
    def test_normalize_invoke_agent_parameters_parses_json_payload(self) -> None:
        parameters = normalize_invoke_agent_parameters(
            agent_name="KnowledgeAgent",
            prompt='{"prompt":"analyze this image","temperature":0.1}',
            state={},
        )

        self.assertEqual(parameters["prompt"], "analyze this image")
        self.assertEqual(parameters["temperature"], 0.1)

    def test_normalize_invoke_agent_parameters_falls_back_for_search_agent(self) -> None:
        parameters = normalize_invoke_agent_parameters(
            agent_name="SearchAgent",
            prompt="cats in snow",
            state={},
        )

        self.assertEqual(parameters["query"], "cats in snow")
        self.assertEqual(parameters["mode"], "all")

    def test_normalize_invoke_agent_parameters_uses_expert_defaults(self) -> None:
        parameters = normalize_invoke_agent_parameters(
            agent_name="ImageGenerationAgent",
            prompt="make a cat poster",
            state={},
        )

        self.assertEqual(parameters["prompt"], "make a cat poster")
        self.assertEqual(parameters["provider"], "nano_banana")
        self.assertEqual(parameters["aspect_ratio"], "16:9")
        self.assertEqual(parameters["resolution"], "1K")

    def test_normalize_invoke_agent_parameters_uses_video_expert_defaults(self) -> None:
        parameters = normalize_invoke_agent_parameters(
            agent_name="VideoGenerationAgent",
            prompt="make a cinematic cat video",
            state={},
        )

        self.assertEqual(parameters["prompt"], "make a cinematic cat video")
        self.assertEqual(parameters["provider"], "seedance")
        self.assertEqual(parameters["mode"], "prompt")
        self.assertEqual(parameters["aspect_ratio"], "16:9")
        self.assertNotIn("resolution", parameters)
        self.assertNotIn("duration_seconds", parameters)

    def test_normalize_invoke_agent_parameters_accepts_kling_multi_reference(self) -> None:
        with patch("src.runtime.expert_dispatcher.resolve_workspace_path", return_value=Path.cwd()):
            parameters = normalize_invoke_agent_parameters(
                agent_name="VideoGenerationAgent",
                prompt=(
                    '{"provider":"kling","mode":"multi_reference","input_paths":["generated/a.png","generated/b.png"],'
                    '"duration_seconds":5,"kling_mode":"pro"}'
                ),
                state={},
            )

        self.assertEqual(parameters["provider"], "kling")
        self.assertEqual(parameters["mode"], "multi_reference")
        self.assertEqual(parameters["duration_seconds"], 5)
        self.assertEqual(parameters["kling_mode"], "pro")

    def test_normalize_invoke_agent_parameters_rejects_invalid_veo_duration(self) -> None:
        with self.assertRaisesRegex(ValueError, "provider `veo` does not support `duration_seconds=16`"):
            normalize_invoke_agent_parameters(
                agent_name="VideoGenerationAgent",
                prompt='{"prompt":"cats","provider":"veo","duration_seconds":16}',
                state={},
            )

    def test_normalize_invoke_agent_parameters_rejects_resolution_for_seedance(self) -> None:
        with self.assertRaisesRegex(ValueError, "parameter `resolution` is not supported for provider `seedance`"):
            normalize_invoke_agent_parameters(
                agent_name="VideoGenerationAgent",
                prompt='{"prompt":"cats","provider":"seedance","resolution":"720p"}',
                state={},
            )

    def test_build_expert_contract_summary_uses_video_capability_notes(self) -> None:
        summary = build_expert_contract_summary()

        self.assertIn("provider `veo`", summary)
        self.assertIn("veo-3.1-generate-preview", summary)
        self.assertIn("native synchronized audio", summary)
        self.assertIn("does not return subtitle/SRT files", summary)
        self.assertIn("['4', '6', '8']", summary)
        self.assertIn("provider `seedance`", summary)
        self.assertIn("visual-only", summary)
        self.assertIn("kling-v1-6", summary)

    def test_normalize_invoke_agent_parameters_uses_3d_generation_defaults(self) -> None:
        parameters = normalize_invoke_agent_parameters(
            agent_name="3DGeneration",
            prompt="make a wooden corgi figurine",
            state={},
        )

        self.assertEqual(parameters["prompt"], "make a wooden corgi figurine")
        self.assertEqual(parameters["provider"], "hy3d")
        self.assertEqual(parameters["model"], "3.0")
        self.assertEqual(parameters["generate_type"], "normal")
        self.assertEqual(parameters["timeout_seconds"], 900)
        self.assertEqual(parameters["interval_seconds"], 8)

    def test_normalize_invoke_agent_parameters_requires_structured_payload_for_image_segmentation(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires structured invoke_agent parameters"):
            normalize_invoke_agent_parameters(
                agent_name="ImageSegmentationAgent",
                prompt="segment the person",
                state={},
            )

    def test_normalize_invoke_agent_parameters_requires_structured_payload_for_image_understanding(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires structured invoke_agent parameters"):
            normalize_invoke_agent_parameters(
                agent_name="ImageUnderstandingAgent",
                prompt="describe this image",
                state={},
            )

    def test_normalize_invoke_agent_parameters_requires_structured_payload_for_image_basic_operations(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires structured invoke_agent parameters"):
            normalize_invoke_agent_parameters(
                agent_name="ImageBasicOperations",
                prompt="rotate this image",
                state={},
            )

    def test_normalize_invoke_agent_parameters_rejects_invalid_search_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid `mode` value"):
            normalize_invoke_agent_parameters(
                agent_name="SearchAgent",
                prompt='{"query":"cats","mode":"weird"}',
                state={},
            )

    def test_normalize_invoke_agent_parameters_rejects_invalid_video_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid `provider` value"):
            normalize_invoke_agent_parameters(
                agent_name="VideoGenerationAgent",
                prompt='{"prompt":"cats","provider":"weird"}',
                state={},
            )

    def test_normalize_invoke_agent_parameters_rejects_invalid_3d_generate_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid `generate_type` value"):
            normalize_invoke_agent_parameters(
                agent_name="3DGeneration",
                prompt='{"prompt":"cats","generate_type":"weird"}',
                state={},
            )

    def test_normalize_invoke_agent_parameters_rejects_invalid_video_basic_operation(self) -> None:
        with (
            patch("src.runtime.expert_dispatcher.resolve_workspace_path", return_value=Path.cwd()),
            self.assertRaisesRegex(ValueError, "invalid `operation` value"),
        ):
            normalize_invoke_agent_parameters(
                agent_name="VideoBasicOperations",
                prompt='{"operation":"weird","input_path":"generated/demo.mp4"}',
                state={},
            )

    async def test_dispatch_expert_call_updates_parent_state(self) -> None:
        artifact_service = InMemoryArtifactService()
        parent_state = State(
            {
                "step": 0,
                "files_history": [],
                "summary_history": [],
                "text_history": [],
                "message_history": [],
                "expert_history": [],
            },
            {},
        )
        tool_context = SimpleNamespace(
            state=parent_state,
            _invocation_context=SimpleNamespace(user_id="user-1"),
        )
        result = await dispatch_expert_call(
            agent_name="KnowledgeAgent",
            prompt='{"prompt":"analyze the request"}',
            tool_context=tool_context,
            expert_agents={"KnowledgeAgent": _FakeExpertAgent(name="KnowledgeAgent")},
            app_name="creative-claw-test",
            artifact_service=artifact_service,
        )

        self.assertEqual(result.tool_result["status"], "success")
        self.assertEqual(parent_state["step"], 0)
        self.assertEqual(parent_state["current_output"]["message"], "expert finished")
        self.assertEqual(parent_state["last_expert_result"]["agent_name"], "KnowledgeAgent")
        self.assertEqual(parent_state["text_history"][-1], "expert answer")
        self.assertEqual(parent_state["summary_history"], ["KnowledgeAgent: expert finished"])
        self.assertEqual(parent_state["app:shared_setting"], "from-child")
        self.assertEqual(parent_state["custom_key"], "custom-value")
        self.assertEqual(result.tool_result["structured_data"]["custom_key"], "custom-value")

    async def test_dispatch_expert_call_filters_internal_parent_state_before_child_run(self) -> None:
        artifact_service = InMemoryArtifactService()
        parent_state = State(
            {
                "step": 3,
                "user_prompt": "describe the image",
                "_adk_hidden": "should-not-leak",
                "files_history": [],
                "summary_history": [],
                "text_history": [],
                "message_history": [],
                "expert_history": [],
            },
            {},
        )
        tool_context = SimpleNamespace(
            state=parent_state,
            _invocation_context=SimpleNamespace(user_id="user-1"),
        )
        result = await dispatch_expert_call(
            agent_name="KnowledgeAgent",
            prompt='{"prompt":"inspect the session"}',
            tool_context=tool_context,
            expert_agents={"KnowledgeAgent": _ParentStateInspectingExpertAgent(name="KnowledgeAgent")},
            app_name="creative-claw-test",
            artifact_service=artifact_service,
        )

        self.assertEqual(result.tool_result["status"], "success")
        self.assertEqual(
            parent_state["current_output"]["message"],
            "child saw filtered state",
        )


if __name__ == "__main__":
    unittest.main()
