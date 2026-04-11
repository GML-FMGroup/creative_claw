import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from google.adk.events import Event, EventActions
from google.adk.runners import Runner

from conf.system import SYS_CONFIG
from src.runtime.models import InboundMessage, MessageAttachment
from src.runtime.workflow_service import CreativeClawRuntime


class RuntimeSessionTests(unittest.IsolatedAsyncioTestCase):
    def test_runtime_registers_image_to_prompt_expert(self) -> None:
        runtime = CreativeClawRuntime()
        image_to_prompt_agent = runtime.expert_agents["ImageToPromptAgent"]

        self.assertIn("ImageToPromptAgent", runtime.expert_agents)
        self.assertEqual(
            getattr(image_to_prompt_agent, "_adk_origin_app_name", None),
            SYS_CONFIG.app_name,
        )
        self.assertIsNotNone(getattr(image_to_prompt_agent, "_adk_origin_path", None))

    def test_runtime_expert_metadata_keeps_runner_app_alignment_clean(self) -> None:
        runtime = CreativeClawRuntime()

        runner = Runner(
            agent=runtime.expert_agents["KnowledgeAgent"],
            app_name=SYS_CONFIG.app_name,
            session_service=runtime.session_service,
            artifact_service=runtime.artifact_service,
        )

        self.assertIsNone(runner._app_name_alignment_hint)

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

    async def test_initial_state_uses_runtime_fields(self) -> None:
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
        self.assertEqual(session.state["final_response"], "")
        self.assertEqual(session.state["current_parameters"], {})
        self.assertIsNone(session.state["current_output"])
        self.assertIsNone(session.state["last_expert_result"])
        self.assertEqual(session.state["expert_history"], [])
        self.assertEqual(session.state["input_files"], [])
        self.assertEqual(session.state["new_files"], [])

    async def test_initial_state_persists_uploaded_files_in_history(self) -> None:
        runtime = CreativeClawRuntime()
        with tempfile.TemporaryDirectory() as tmpdir:
            upload_path = Path(tmpdir) / "demo.png"
            upload_path.write_bytes(b"fake-image")
            inbound = InboundMessage(
                channel="local",
                sender_id="local-user",
                chat_id="terminal",
                text="describe this image",
                attachments=[
                    MessageAttachment(
                        path=str(upload_path),
                        name="demo.png",
                        mime_type="image/png",
                        description="uploaded test image",
                    )
                ],
            )

            user_id, session_id = await runtime._ensure_session(inbound)
            await runtime._set_initial_state(user_id, session_id, inbound)
            session = await runtime.session_service.get_session(
                app_name=SYS_CONFIG.app_name,
                user_id=user_id,
                session_id=session_id,
            )

        self.assertEqual(len(session.state["input_files"]), 1)
        self.assertEqual(len(session.state["files_history"]), 1)
        self.assertEqual(session.state["files_history"][0][0]["source"], "channel")
        self.assertTrue(session.state["input_files"][0]["path"].startswith("inbox/local/"))

    async def test_run_message_uses_natural_progress_messages(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="local",
            sender_id="local-user",
            chat_id="terminal",
            text="Generate an image for me",
        )

        class _FakeOrchestrator:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def run_until_done(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "Image generation is complete.",
                    "final_response": "The image is ready.",
                    "last_output_message": "The image is ready.",
                    "new_orchestration_events": [],
                }

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(events[0].event_type, "status")
        self.assertEqual(events[0].text, "I'll start processing your request.")
        self.assertEqual(events[0].metadata["stage_title"], "Starting")
        self.assertEqual(events[-1].event_type, "final")
        self.assertEqual(events[-1].text, "The image is ready.")
        self.assertNotIn("Image generation is complete.", events[-1].text)

    async def test_run_message_emits_granular_orchestration_events(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="local",
            sender_id="local-user",
            chat_id="terminal",
            text="Analyze this directory",
        )

        class _FakeOrchestrator:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def run_until_done(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "The analysis is ready.",
                    "last_output_message": "internal-output",
                    "final_response": "The analysis is ready.",
                    "new_orchestration_events": [
                        {
                            "title": "List Skills",
                            "detail": "Checking the currently available skills.",
                            "stage": "planning",
                        },
                        {
                            "title": "invoke_agent",
                            "detail": "Status: success\nArgs: agent_name=KnowledgeAgent; prompt={\"prompt\":\"analyze\"}\nResult: KnowledgeAgent finished with status=success; message=done",
                            "stage": "expert_execution",
                        },
                    ],
                }

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        progress_events = [event for event in events if event.event_type == "status"]
        self.assertEqual(progress_events[1].metadata["stage_title"], "List Skills")
        self.assertEqual(progress_events[1].metadata["stage"], "planning")
        self.assertIn("Checking the currently available skills.", progress_events[1].text)
        self.assertEqual(progress_events[2].metadata["stage_title"], "invoke_agent")
        self.assertEqual(progress_events[2].metadata["stage"], "expert_execution")
        self.assertIn("1. List Skills", progress_events[2].text)
        self.assertIn("2. invoke_agent", progress_events[2].text)

    async def test_run_message_renders_tool_args_and_result_summary(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="local",
            sender_id="local-user",
            chat_id="terminal",
            text="Check this file",
        )

        class _FakeOrchestrator:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def run_until_done(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "Done.",
                    "final_response": "Done.",
                    "last_output_message": "",
                    "new_orchestration_events": [
                        {
                            "title": "read_file",
                            "detail": "Status: started\nArgs: path=README.md",
                            "stage": "inspection",
                        },
                        {
                            "title": "read_file",
                            "detail": "Status: success\nArgs: path=README.md\nResult: Hello world",
                            "stage": "inspection",
                        },
                    ],
                }

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        progress_events = [event for event in events if event.event_type == "status"]
        self.assertIn("Args: path=README.md", progress_events[-1].text)
        self.assertIn("Result: Hello world", progress_events[-1].text)

    async def test_run_message_keeps_smart_tool_summary_in_timeline(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="local",
            sender_id="local-user",
            chat_id="terminal",
            text="List this directory",
        )

        class _FakeOrchestrator:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def run_until_done(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "Done.",
                    "final_response": "Done.",
                    "last_output_message": "",
                    "new_orchestration_events": [
                        {
                            "title": "list_dir",
                            "detail": "Status: started\nArgs: path=.",
                            "stage": "inspection",
                        },
                        {
                            "title": "list_dir",
                            "detail": "Status: success\nArgs: path=.\nResult: 3 entries. Preview: [D] src; [F] README.md; [F] pyproject.toml",
                            "stage": "inspection",
                        },
                    ],
                }

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        progress_events = [event for event in events if event.event_type == "status"]
        self.assertIn("3 entries", progress_events[-1].text)
        self.assertIn("README.md", progress_events[-1].text)

    async def test_build_final_event_prefers_state_final_response_over_text_history(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="local",
            sender_id="local-user",
            chat_id="terminal",
            text="hello",
        )

        user_id, session_id = await runtime._ensure_session(inbound)
        session = await runtime.session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=user_id,
            session_id=session_id,
        )
        await runtime.session_service.append_event(
            session,
            Event(
                author="unit_test",
                actions=EventActions(
                    state_delta={
                        "files_history": [],
                        "text_history": ["这是 expert 的长输出。"],
                        "summary_history": [],
                        "final_summary": "Internal completion summary.",
                        "final_response": "这是给用户看的最终回复。",
                    }
                ),
            ),
        )

        final_event = await runtime._build_final_event(
            user_id=user_id,
            session_id=session_id,
            final_summary="fallback reply",
        )

        self.assertEqual(final_event.event_type, "final")
        self.assertEqual(final_event.text, "这是给用户看的最终回复。")

    async def test_run_message_surfaces_orchestrator_failure(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="local",
            sender_id="local-user",
            chat_id="terminal",
            text="Describe this image",
        )

        class _FakeOrchestrator:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def run_until_done(self) -> dict:
                raise KeyError("error")

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
            events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(events[-1].event_type, "error")
        self.assertIn("Workflow failed", events[-1].text)
        self.assertIn("session_id=", events[-1].text)
        self.assertIn("KeyError: 'error'", events[-1].text)

    async def test_run_message_does_not_resend_channel_only_upload_as_final_artifact(self) -> None:
        runtime = CreativeClawRuntime()
        with tempfile.TemporaryDirectory() as tmpdir:
            upload_path = Path(tmpdir) / "demo.png"
            upload_path.write_bytes(b"fake-image")
            inbound = InboundMessage(
                channel="local",
                sender_id="local-user",
                chat_id="terminal",
                text="Describe this image",
                attachments=[MessageAttachment(path=str(upload_path), name="demo.png", mime_type="image/png")],
            )

            class _FakeOrchestrator:
                def __init__(self, **_kwargs) -> None:
                    self.uid = ""
                    self.sid = ""

                async def run_until_done(self) -> dict:
                    return {
                        "workflow_status": "finished",
                        "final_summary": "Image description completed.",
                        "final_response": "Image description completed.",
                        "last_output_message": "Image description completed.",
                        "new_orchestration_events": [],
                    }

            with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
                events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(events[-1].event_type, "final")
        self.assertEqual(events[-1].artifact_paths, [])


if __name__ == "__main__":
    unittest.main()
