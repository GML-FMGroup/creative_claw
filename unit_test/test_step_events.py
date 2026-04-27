import unittest
import asyncio
from types import SimpleNamespace

from src.runtime.step_events import (
    CreativeClawStepEventPlugin,
    configure_step_event_publisher,
    publish_orchestration_step_event,
    reset_step_event_history,
)
from src.runtime.tool_context import route_context


class StepEventPluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.messages = []

        async def _publisher(message):
            self.messages.append(message)

        configure_step_event_publisher(_publisher)

    async def asyncTearDown(self) -> None:
        configure_step_event_publisher(None)

    async def test_plugin_publishes_realtime_tool_start_and_finish(self) -> None:
        plugin = CreativeClawStepEventPlugin()
        invocation = SimpleNamespace(invocation_id="inv-1")
        tool = SimpleNamespace(name="read_file")
        tool_context = SimpleNamespace(
            invocation_id="inv-1",
            session=SimpleNamespace(id="session-1", state={"turn_index": 4}),
        )

        with route_context("cli", "chat-1"):
            await plugin.before_run_callback(invocation_context=invocation)
            await plugin.before_tool_callback(
                tool=tool,
                tool_args={"path": "README.md"},
                tool_context=tool_context,
            )
            await plugin.after_tool_callback(
                tool=tool,
                tool_args={"path": "README.md"},
                tool_context=tool_context,
                result="line one\nline two\nline three",
            )
            await plugin.after_run_callback(invocation_context=invocation)

        self.assertEqual(len(self.messages), 2)
        self.assertEqual(self.messages[0].metadata["stage_title"], "read_file")
        self.assertEqual(self.messages[0].metadata["turn_index"], 4)
        self.assertIn("Status: started", self.messages[0].text)
        self.assertIn("Args: path=README.md", self.messages[0].text)
        self.assertIn("1. read_file", self.messages[1].text)
        self.assertIn("2. read_file", self.messages[1].text)
        self.assertIn("Result: Read succeeded", self.messages[1].text)

    async def test_plugin_publishes_short_video_production_progress(self) -> None:
        plugin = CreativeClawStepEventPlugin()
        invocation = SimpleNamespace(invocation_id="inv-short-video")
        tool = SimpleNamespace(name="run_short_video_production")
        tool_context = SimpleNamespace(
            invocation_id="inv-short-video",
            session=SimpleNamespace(id="session-short-video", state={"turn_index": 3}),
        )

        with route_context("feishu", "chat-short-video"):
            await plugin.before_run_callback(invocation_context=invocation)
            await plugin.before_tool_callback(
                tool=tool,
                tool_args={"action": "start", "placeholder_assets": False},
                tool_context=tool_context,
            )
            await plugin.after_tool_callback(
                tool=tool,
                tool_args={"action": "start", "placeholder_assets": False},
                tool_context=tool_context,
                result={
                    "status": "needs_user_review",
                    "capability": "short_video",
                    "stage": "asset_plan_review",
                    "progress_percent": 20,
                    "message": "Please review the short-video asset plan.",
                },
            )
            await plugin.after_run_callback(invocation_context=invocation)

        self.assertEqual(len(self.messages), 2)
        self.assertEqual(self.messages[0].metadata["stage"], "video_processing")
        self.assertEqual(self.messages[0].metadata["stage_title"], "run_short_video_production")
        self.assertIn("Status: started", self.messages[0].text)
        self.assertIn("stage=asset_plan_review", self.messages[1].text)

    async def test_plugin_publishes_ppt_production_progress(self) -> None:
        plugin = CreativeClawStepEventPlugin()
        invocation = SimpleNamespace(invocation_id="inv-ppt")
        tool = SimpleNamespace(name="run_ppt_production")
        tool_context = SimpleNamespace(
            invocation_id="inv-ppt",
            session=SimpleNamespace(id="session-ppt", state={"turn_index": 4}),
        )

        with route_context("web", "chat-ppt"):
            await plugin.before_run_callback(invocation_context=invocation)
            await plugin.before_tool_callback(
                tool=tool,
                tool_args={"action": "start", "placeholder_ppt": True},
                tool_context=tool_context,
            )
            await plugin.after_tool_callback(
                tool=tool,
                tool_args={"action": "start", "placeholder_ppt": True},
                tool_context=tool_context,
                result={
                    "status": "needs_user_review",
                    "capability": "ppt",
                    "stage": "outline_review",
                    "progress_percent": 30,
                    "message": "Please review the PPT outline.",
                },
            )
            await plugin.after_run_callback(invocation_context=invocation)

        self.assertEqual(len(self.messages), 2)
        self.assertEqual(self.messages[0].metadata["stage"], "ppt_processing")
        self.assertEqual(self.messages[0].metadata["stage_title"], "run_ppt_production")
        self.assertIn("Status: started", self.messages[0].text)
        self.assertIn("ppt status=needs_user_review", self.messages[1].text)

    async def test_plugin_publishes_design_production_progress(self) -> None:
        plugin = CreativeClawStepEventPlugin()
        invocation = SimpleNamespace(invocation_id="inv-design")
        tool = SimpleNamespace(name="run_design_production")
        tool_context = SimpleNamespace(
            invocation_id="inv-design",
            session=SimpleNamespace(id="session-design", state={"turn_index": 5}),
        )

        with route_context("web", "chat-design"):
            await plugin.before_run_callback(invocation_context=invocation)
            await plugin.before_tool_callback(
                tool=tool,
                tool_args={"action": "start", "placeholder_design": True},
                tool_context=tool_context,
            )
            await plugin.after_tool_callback(
                tool=tool,
                tool_args={"action": "start", "placeholder_design": True},
                tool_context=tool_context,
                result={
                    "status": "completed",
                    "capability": "design",
                    "stage": "completed",
                    "progress_percent": 100,
                    "message": "Design production completed.",
                },
            )
            await plugin.after_run_callback(invocation_context=invocation)

        self.assertEqual(len(self.messages), 2)
        self.assertEqual(self.messages[0].metadata["stage"], "design_processing")
        self.assertEqual(self.messages[0].metadata["stage_title"], "run_design_production")
        self.assertIn("Status: started", self.messages[0].text)
        self.assertIn("design status=completed", self.messages[1].text)

    async def test_plugin_ignores_unknown_tool_names(self) -> None:
        plugin = CreativeClawStepEventPlugin()
        invocation = SimpleNamespace(invocation_id="inv-2")
        tool = SimpleNamespace(name="run_expert")
        tool_context = SimpleNamespace(
            invocation_id="inv-2",
            session=SimpleNamespace(id="session-2"),
        )

        with route_context("cli", "chat-2"):
            await plugin.before_run_callback(invocation_context=invocation)
            await plugin.before_tool_callback(
                tool=tool,
                tool_args={"agent_name": "KnowledgeAgent"},
                tool_context=tool_context,
            )
            await plugin.after_run_callback(invocation_context=invocation)

        self.assertEqual(self.messages, [])

    async def test_orchestration_event_is_published_realtime(self) -> None:
        with route_context("cli", "chat-3"):
            reset_step_event_history(session_id="session-3")
            publish_orchestration_step_event(
                session_id="session-3",
                title="Call Expert Agent",
                detail="Calling `ImageGenerationAgent` for the current step.",
                stage="expert_execution",
            )
            await asyncio.sleep(0)

        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.messages[0].metadata["stage_title"], "Call Expert Agent")
        self.assertIn("Calling `ImageGenerationAgent`", self.messages[0].text)

    async def test_orchestration_event_history_is_scoped_by_turn_index(self) -> None:
        with route_context("cli", "chat-3"):
            reset_step_event_history(session_id="session-3", turn_index=1)
            publish_orchestration_step_event(
                session_id="session-3",
                turn_index=1,
                title="First Turn Expert",
                detail="Running the first request.",
                stage="expert_execution",
            )
            publish_orchestration_step_event(
                session_id="session-3",
                turn_index=2,
                title="Second Turn Expert",
                detail="Running the second request.",
                stage="expert_execution",
            )
            await asyncio.sleep(0)

        self.assertEqual(len(self.messages), 2)
        self.assertEqual(self.messages[0].metadata["turn_index"], 1)
        self.assertEqual(self.messages[1].metadata["turn_index"], 2)
        self.assertIn("First Turn Expert", self.messages[0].text)
        self.assertIn("Second Turn Expert", self.messages[1].text)
        self.assertNotIn("First Turn Expert", self.messages[1].text)

    async def test_plugin_and_orchestration_events_share_same_history(self) -> None:
        plugin = CreativeClawStepEventPlugin()
        invocation = SimpleNamespace(invocation_id="inv-4")
        tool = SimpleNamespace(name="read_file")
        tool_context = SimpleNamespace(
            invocation_id="inv-4",
            session=SimpleNamespace(id="session-4"),
        )

        with route_context("cli", "chat-4"):
            reset_step_event_history(session_id="session-4")
            publish_orchestration_step_event(
                session_id="session-4",
                title="Call Expert Agent",
                detail="Calling `KnowledgeAgent` for the current step.",
                stage="expert_execution",
            )
            await asyncio.sleep(0)
            await plugin.before_tool_callback(
                tool=tool,
                tool_args={"path": "README.md"},
                tool_context=tool_context,
            )

        self.assertEqual(len(self.messages), 2)
        self.assertIn("1. Call Expert Agent", self.messages[1].text)
        self.assertIn("2. read_file", self.messages[1].text)
