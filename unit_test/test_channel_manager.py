import unittest

from src.channels.manager import ChannelManager
from src.channels.local import LocalChannel
from src.runtime.models import InboundMessage, WorkflowEvent
from src.runtime.tool_context import get_route


class _FakeRuntime:
    async def run_message(self, _message: InboundMessage):
        yield WorkflowEvent(event_type="status", text="working")
        yield WorkflowEvent(event_type="final", text="done", artifact_paths=["outputs/demo.png"])


class _RouteAwareRuntime:
    def __init__(self) -> None:
        self.route = None

    async def run_message(self, _message: InboundMessage):
        self.route = get_route()
        yield WorkflowEvent(event_type="final", text="done")


class ChannelManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_manager_routes_runtime_events_to_registered_channel(self) -> None:
        lines: list[str] = []
        manager = ChannelManager(runtime=_FakeRuntime())  # type: ignore[arg-type]
        manager.register(LocalChannel(writer=lines.append))

        await manager.handle_inbound(
            InboundMessage(
                channel="local",
                sender_id="u1",
                chat_id="c1",
                text="hello",
            )
        )

        self.assertEqual(
            lines,
            [
                "working",
                "done",
                "[artifact] outputs/demo.png",
            ],
        )

    async def test_manager_formats_error_message_for_user(self) -> None:
        class _ErrorRuntime:
            async def run_message(self, _message: InboundMessage):
                yield WorkflowEvent(event_type="error", text="boom")

        lines: list[str] = []
        manager = ChannelManager(runtime=_ErrorRuntime())  # type: ignore[arg-type]
        manager.register(LocalChannel(writer=lines.append))

        await manager.handle_inbound(
            InboundMessage(
                channel="local",
                sender_id="u1",
                chat_id="c1",
                text="hello",
            )
        )

        self.assertEqual(lines, ["处理失败：boom"])

    async def test_manager_rejects_unknown_channel(self) -> None:
        manager = ChannelManager(runtime=_FakeRuntime())  # type: ignore[arg-type]

        with self.assertRaises(ValueError):
            await manager.handle_inbound(
                InboundMessage(
                    channel="local",
                    sender_id="u1",
                    chat_id="c1",
                    text="hello",
                )
            )

    async def test_manager_exposes_route_context_during_runtime_execution(self) -> None:
        runtime = _RouteAwareRuntime()
        lines: list[str] = []
        manager = ChannelManager(runtime=runtime)  # type: ignore[arg-type]
        manager.register(LocalChannel(writer=lines.append))

        await manager.handle_inbound(
            InboundMessage(
                channel="local",
                sender_id="u1",
                chat_id="c9",
                text="hello",
            )
        )

        self.assertEqual(runtime.route, ("local", "c9"))
        self.assertEqual(lines, ["done"])


if __name__ == "__main__":
    unittest.main()
