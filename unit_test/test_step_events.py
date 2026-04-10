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
            session=SimpleNamespace(id="session-1"),
        )

        with route_context("local", "chat-1"):
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
        self.assertIn("Status: started", self.messages[0].text)
        self.assertIn("Args: path=README.md", self.messages[0].text)
        self.assertIn("1. read_file", self.messages[1].text)
        self.assertIn("2. read_file", self.messages[1].text)
        self.assertIn("Result: Read succeeded", self.messages[1].text)

    async def test_plugin_ignores_unknown_tool_names(self) -> None:
        plugin = CreativeClawStepEventPlugin()
        invocation = SimpleNamespace(invocation_id="inv-2")
        tool = SimpleNamespace(name="run_expert")
        tool_context = SimpleNamespace(
            invocation_id="inv-2",
            session=SimpleNamespace(id="session-2"),
        )

        with route_context("local", "chat-2"):
            await plugin.before_run_callback(invocation_context=invocation)
            await plugin.before_tool_callback(
                tool=tool,
                tool_args={"agent_name": "KnowledgeAgent"},
                tool_context=tool_context,
            )
            await plugin.after_run_callback(invocation_context=invocation)

        self.assertEqual(self.messages, [])

    async def test_orchestration_event_is_published_realtime(self) -> None:
        with route_context("local", "chat-3"):
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

    async def test_plugin_and_orchestration_events_share_same_history(self) -> None:
        plugin = CreativeClawStepEventPlugin()
        invocation = SimpleNamespace(invocation_id="inv-4")
        tool = SimpleNamespace(name="read_file")
        tool_context = SimpleNamespace(
            invocation_id="inv-4",
            session=SimpleNamespace(id="session-4"),
        )

        with route_context("local", "chat-4"):
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
