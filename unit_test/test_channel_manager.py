import unittest

from src.channels.manager import ChannelManager
from src.channels.local import LocalChannel
from src.runtime.models import InboundMessage, WorkflowEvent


class _FakeRuntime:
    async def run_message(self, _message: InboundMessage):
        yield WorkflowEvent(event_type="status", text="working")
        yield WorkflowEvent(event_type="final", text="done", artifact_paths=["outputs/demo.png"])


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
                "[status] working",
                "[final] done",
                "[artifact] outputs/demo.png",
            ],
        )

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


if __name__ == "__main__":
    unittest.main()
