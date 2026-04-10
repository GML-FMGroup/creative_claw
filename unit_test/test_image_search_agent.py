import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.agents.experts.search.search_agent import SearchAgent
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


class SearchAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_rejects_missing_required_parameters(self) -> None:
        agent = SearchAgent(name="SearchAgent")
        ctx = _build_ctx({"current_parameters": {"query": "cat"}, "step": 0})

        events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("must include: query, mode", current_output["message"])

    async def test_search_image_persists_workspace_output_files(self) -> None:
        agent = SearchAgent(name="SearchAgent")
        ctx = _build_ctx({"current_parameters": {"query": "blue cat", "mode": "image"}, "step": 0})

        with (
            patch(
                "src.agents.experts.search.search_agent.retrieve_image_by_text",
                new=AsyncMock(return_value={"status": "success", "message": [b"png-data"]}),
            ),
            patch(
                "src.agents.experts.search.search_agent.save_binary_output",
                return_value=workspace_root() / "generated" / "session_1" / "step1_search_output0.png",
            ),
        ):
            current_output = await agent.search_image(ctx)

        self.assertEqual(current_output["status"], "success")
        self.assertEqual(current_output["output_files"][0]["path"], "generated/session_1/step1_search_output0.png")
        self.assertIn("step1_search_output0.png", current_output["message"])

    async def test_invalid_mode_falls_back_to_all_and_combines_results(self) -> None:
        agent = SearchAgent(name="SearchAgent")
        ctx = _build_ctx({"current_parameters": {"query": "poster", "mode": "weird"}, "step": 0})

        with (
            patch.object(
                SearchAgent,
                "search_image",
                new=AsyncMock(return_value={"status": "success", "message": "image ok", "output_files": [{"path": "generated/session_1/x.png"}]}),
            ) as image_mock,
            patch.object(
                SearchAgent,
                "search_text",
                new=AsyncMock(return_value={"status": "success", "message": "text ok"}),
            ) as text_mock,
        ):
            events = [event async for event in agent._run_async_impl(ctx)]

        image_mock.assert_awaited_once()
        text_mock.assert_awaited_once()
        self.assertEqual(ctx.session.state["current_parameters"]["mode"], "all")
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "success")
        self.assertIn("has competed text retrieval", current_output["message"])
        self.assertEqual(current_output["output_files"][0]["path"], "generated/session_1/x.png")


if __name__ == "__main__":
    unittest.main()
