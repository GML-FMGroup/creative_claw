import unittest
from types import SimpleNamespace

from google.adk.agents import BaseAgent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
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
                    "current_output": {
                        "status": "success",
                        "message": "expert finished",
                        "output_text": "expert answer",
                    },
                    "custom_key": "custom-value",
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
        expert_runner = Runner(
            agent=_FakeExpertAgent(name="KnowledgeAgent"),
            app_name="creative-claw-test",
            session_service=InMemorySessionService(),
            artifact_service=artifact_service,
        )

        result = await dispatch_expert_call(
            agent_name="KnowledgeAgent",
            prompt='{"prompt":"analyze the request"}',
            tool_context=tool_context,
            expert_runners={"KnowledgeAgent": expert_runner},
            app_name="creative-claw-test",
            artifact_service=artifact_service,
        )

        self.assertEqual(result.tool_result["status"], "success")
        self.assertEqual(parent_state["step"], 1)
        self.assertEqual(parent_state["current_output"]["message"], "expert finished")
        self.assertEqual(parent_state["last_expert_result"]["agent_name"], "KnowledgeAgent")
        self.assertEqual(parent_state["text_history"][-1], "expert answer")
        self.assertEqual(parent_state["custom_key"], "custom-value")


if __name__ == "__main__":
    unittest.main()
