import asyncio
import unittest
from types import SimpleNamespace

from src.production.design.tool import run_design_production
from src.production.ppt.tool import run_ppt_production
from src.production.short_video.tool import run_short_video_production


def _adk_state() -> dict:
    return {
        "sid": "session_production_isolation",
        "turn_index": 1,
        "step": 0,
        "channel": "cli",
        "chat_id": "terminal",
        "sender_id": "cli-user",
        "uploaded": [],
        "generated": [],
        "files_history": [],
        "final_file_paths": [],
    }


class ProductionSessionIsolationTests(unittest.TestCase):
    def test_tools_default_to_active_session_for_their_own_capability(self) -> None:
        state = _adk_state()
        tool_context = SimpleNamespace(state=state)

        ppt_started = asyncio.run(
            run_ppt_production(
                action="start",
                user_prompt="Build a concise product update deck",
                render_settings={"target_pages": 3},
                tool_context=tool_context,
            )
        )
        design_started = asyncio.run(
            run_design_production(
                action="start",
                user_prompt="Design a product detail page",
                placeholder_design=False,
                tool_context=tool_context,
            )
        )
        short_video_started = asyncio.run(
            run_short_video_production(
                action="start",
                user_prompt="Make a product ad",
                placeholder_assets=False,
                render_settings={"aspect_ratio": "9:16"},
                tool_context=tool_context,
            )
        )

        self.assertEqual(ppt_started["status"], "needs_user_review")
        self.assertEqual(design_started["status"], "needs_user_review")
        self.assertEqual(short_video_started["status"], "needs_user_review")
        self.assertEqual(state["active_production_session_id"], short_video_started["production_session_id"])

        ppt_view = asyncio.run(
            run_ppt_production(action="view", view_type="overview", tool_context=tool_context)
        )
        design_view = asyncio.run(
            run_design_production(action="view", view_type="overview", tool_context=tool_context)
        )
        short_video_view = asyncio.run(
            run_short_video_production(action="view", view_type="overview", tool_context=tool_context)
        )

        self.assertEqual(ppt_view["production_session_id"], ppt_started["production_session_id"])
        self.assertEqual(design_view["production_session_id"], design_started["production_session_id"])
        self.assertEqual(short_video_view["production_session_id"], short_video_started["production_session_id"])

        wrong_capability_view = asyncio.run(
            run_ppt_production(
                action="view",
                production_session_id=design_started["production_session_id"],
                view_type="overview",
                tool_context=tool_context,
            )
        )
        self.assertEqual(wrong_capability_view["status"], "failed")
        self.assertEqual(wrong_capability_view["stage"], "not_found")


if __name__ == "__main__":
    unittest.main()
