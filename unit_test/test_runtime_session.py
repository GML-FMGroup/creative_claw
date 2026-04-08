import unittest
from unittest.mock import patch

from conf.system import SYS_CONFIG
from src.runtime.models import InboundMessage
from src.runtime.workflow_service import CreativeClawRuntime


class RuntimeSessionTests(unittest.IsolatedAsyncioTestCase):
    def test_runtime_registers_image_to_prompt_expert(self) -> None:
        runtime = CreativeClawRuntime()

        self.assertIn("ImageToPromptAgent", runtime.expert_agents)
        self.assertIn("ImageToPromptAgent", runtime.expert_runners)

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

    async def test_reset_session_creates_new_session_for_same_channel_chat_pair(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="local",
            sender_id="local-user",
            chat_id="terminal",
            text="hello",
        )

        _user_id_1, session_id_1 = await runtime._ensure_session(inbound)
        _user_id_2, session_id_2 = await runtime.reset_session(inbound)

        self.assertNotEqual(session_id_1, session_id_2)

        _user_id_3, session_id_3 = await runtime._ensure_session(inbound)
        self.assertEqual(session_id_2, session_id_3)

    async def test_help_command_returns_help_text_without_creating_session(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="local",
            sender_id="local-user",
            chat_id="terminal",
            text="/help",
        )

        events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "final")
        self.assertIn("/new", events[0].text)
        self.assertIn("/help", events[0].text)
        self.assertEqual(runtime._session_keys, {})

    async def test_initial_state_uses_skills_first_runtime_fields(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="local",
            sender_id="local-user",
            chat_id="terminal",
            text="hello",
        )

        user_id, session_id = await runtime._ensure_session(inbound)
        await runtime._set_initial_state(user_id, session_id, inbound)
        session = await runtime.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )

        self.assertEqual(session.state["workflow_status"], "running")
        self.assertEqual(session.state["final_summary"], "")
        self.assertEqual(session.state["current_parameters"], {})
        self.assertIsNone(session.state["current_output"])

    async def test_run_message_uses_natural_progress_messages(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="local",
            sender_id="local-user",
            chat_id="terminal",
            text="帮我生成一张图",
        )

        class _FakeOrchestrator:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def run_step(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "图片已经生成好了。",
                    "last_response": "Internal orchestrator log",
                    "last_output_message": "Internal step result",
                }

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(events[0].event_type, "status")
        self.assertEqual(events[0].text, "我先处理一下你的请求。")
        self.assertEqual(events[0].metadata["stage_title"], "开始处理")
        self.assertEqual(events[-1].event_type, "final")
        self.assertEqual(events[-1].text, "图片已经生成好了。")
        self.assertTrue(all("user instruction:" not in event.text for event in events))
        self.assertTrue(all("Orchestrator response:" not in event.text for event in events))
        self.assertTrue(all("Step result:" not in event.text for event in events))

    async def test_run_message_emits_granular_orchestration_events(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="local",
            sender_id="local-user",
            chat_id="terminal",
            text="帮我分析目录",
        )

        class _FakeOrchestrator:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def run_step(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "已经整理好了。",
                    "last_response": "internal",
                    "last_output_message": "internal-output",
                    "new_orchestration_events": [
                        {
                            "title": "查看技能列表",
                            "detail": "正在检查当前可用的技能。",
                            "stage": "planning",
                        },
                        {
                            "title": "调用专家代理",
                            "detail": "正在调用 `KnowledgeAgent` 处理当前步骤。",
                            "stage": "expert_execution",
                        },
                    ],
                }

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        progress_events = [event for event in events if event.event_type == "status"]
        self.assertEqual(progress_events[1].metadata["stage_title"], "查看技能列表")
        self.assertEqual(progress_events[1].metadata["stage"], "planning")
        self.assertIn("正在检查当前可用的技能。", progress_events[1].text)
        self.assertEqual(progress_events[2].metadata["stage_title"], "调用专家代理")
        self.assertEqual(progress_events[2].metadata["stage"], "expert_execution")
        self.assertIn("1. 查看技能列表", progress_events[2].text)
        self.assertIn("2. 调用专家代理", progress_events[2].text)

    async def test_run_message_renders_tool_args_and_result_summary(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="local",
            sender_id="local-user",
            chat_id="terminal",
            text="帮我查看文件",
        )

        class _FakeOrchestrator:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def run_step(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "完成。",
                    "last_response": "",
                    "last_output_message": "",
                    "new_orchestration_events": [
                        {
                            "title": "read_file",
                            "detail": "状态：开始\n参数：path=README.md",
                            "stage": "inspection",
                        },
                        {
                            "title": "read_file",
                            "detail": "状态：成功\n参数：path=README.md\n结果：Hello world",
                            "stage": "inspection",
                        },
                    ],
                }

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        progress_events = [event for event in events if event.event_type == "status"]
        self.assertIn("参数：path=README.md", progress_events[-1].text)
        self.assertIn("结果：Hello world", progress_events[-1].text)

    async def test_run_message_keeps_smart_tool_summary_in_timeline(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="local",
            sender_id="local-user",
            chat_id="terminal",
            text="帮我列目录",
        )

        class _FakeOrchestrator:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def run_step(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "完成。",
                    "last_response": "",
                    "last_output_message": "",
                    "new_orchestration_events": [
                        {
                            "title": "list_dir",
                            "detail": "状态：开始\n参数：path=.",
                            "stage": "inspection",
                        },
                        {
                            "title": "list_dir",
                            "detail": "状态：成功\n参数：path=.\n结果：共 3 个条目。预览：[D] src; [F] README.md; [F] pyproject.toml",
                            "stage": "inspection",
                        },
                    ],
                }

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        progress_events = [event for event in events if event.event_type == "status"]
        self.assertIn("共 3 个条目", progress_events[-1].text)
        self.assertIn("README.md", progress_events[-1].text)


if __name__ == "__main__":
    unittest.main()
