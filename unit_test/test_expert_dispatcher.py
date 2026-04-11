import unittest
from types import SimpleNamespace

from google.adk.agents import BaseAgent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions
from google.adk.sessions.state import State

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
        self.assertEqual(parameters["resolution"], "720p")

    def test_normalize_invoke_agent_parameters_requires_structured_payload_for_image_understanding(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires structured invoke_agent parameters"):
            normalize_invoke_agent_parameters(
                agent_name="ImageUnderstandingAgent",
                prompt="describe this image",
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
        self.assertEqual(parent_state["step"], 1)
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
