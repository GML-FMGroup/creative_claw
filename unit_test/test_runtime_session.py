import unittest

from src.runtime.models import InboundMessage
from src.runtime.workflow_service import CreativeClawRuntime


class RuntimeSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_session_reuses_same_channel_chat_pair(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="local",
            sender_id="local-user",
            chat_id="terminal",
            text="hello",
        )

        user_id_1, session_id_1 = await runtime._ensure_session(inbound)
        user_id_2, session_id_2 = await runtime._ensure_session(inbound)

        self.assertEqual(user_id_1, user_id_2)
        self.assertEqual(session_id_1, session_id_2)


if __name__ == "__main__":
    unittest.main()
