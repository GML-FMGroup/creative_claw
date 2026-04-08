import unittest

from src.channels.events import OutboundMessage
from src.channels.local import LocalChannel


class LocalChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_prints_message_and_artifacts(self) -> None:
        lines: list[str] = []
        channel = LocalChannel(writer=lines.append)

        await channel.send(
            OutboundMessage(
                channel="local",
                chat_id="terminal",
                text="completed",
                artifact_paths=["outputs/image.png"],
            )
        )

        self.assertEqual(lines, ["completed", "[artifact] outputs/image.png"])


if __name__ == "__main__":
    unittest.main()
