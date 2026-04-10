import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from conf.system import SYS_CONFIG
from src.runtime.models import InboundMessage, MessageAttachment
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

            async def generate_step_plan(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "The image is ready.",
                    "last_response": "Internal orchestrator log",
                    "last_output_message": "The image is ready.",
                    "current_plan": {
                        "next_agent": "FINISH",
                        "parameters": {},
                        "summary": "The image is ready.",
                    },
                }

        class _FakeExecutor:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def execute_plan(self):
                raise AssertionError("Executor should not run when the planner finishes directly.")

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator), patch(
            "src.runtime.workflow_service.Executor", _FakeExecutor
        ):
            events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(events[0].event_type, "status")
        self.assertEqual(events[0].text, "I'll start processing your request.")
        self.assertEqual(events[0].metadata["stage_title"], "Starting")
        self.assertEqual(events[-1].event_type, "final")
        self.assertEqual(events[-1].text, "The image is ready.")
        self.assertTrue(all("user instruction:" not in event.text for event in events))
        self.assertTrue(all("Orchestrator response:" not in event.text for event in events))
        self.assertTrue(all("Step result:" not in event.text for event in events))

    async def test_run_message_surfaces_init_exception_type_in_error_event(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="local",
            sender_id="local-user",
            chat_id="terminal",
            text="hello",
        )

        async def _boom(*_args, **_kwargs) -> None:
            raise FileNotFoundError("missing upload")

        with patch.object(runtime, "_set_initial_state", _boom):
            events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(events[-1].event_type, "error")
        self.assertIn("Init state failed", events[-1].text)
        self.assertIn("FileNotFoundError: missing upload", events[-1].text)

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

            async def generate_step_plan(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "The analysis is ready.",
                    "last_response": "internal",
                    "last_output_message": "internal-output",
                    "current_plan": {
                        "next_agent": "FINISH",
                        "parameters": {},
                        "summary": "The analysis is ready.",
                    },
                    "new_orchestration_events": [
                        {
                            "title": "List Skills",
                            "detail": "Checking the currently available skills.",
                            "stage": "planning",
                        },
                        {
                            "title": "Call Expert Agent",
                            "detail": "Calling `KnowledgeAgent` for the current step.",
                            "stage": "expert_execution",
                        },
                    ],
                }

        class _FakeExecutor:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def execute_plan(self):
                raise AssertionError("Executor should not run when the planner finishes directly.")

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator), patch(
            "src.runtime.workflow_service.Executor", _FakeExecutor
        ):
            events = [event async for event in runtime.run_message(inbound)]

        progress_events = [event for event in events if event.event_type == "status"]
        self.assertEqual(progress_events[1].metadata["stage_title"], "List Skills")
        self.assertEqual(progress_events[1].metadata["stage"], "planning")
        self.assertIn("Checking the currently available skills.", progress_events[1].text)
        self.assertEqual(progress_events[2].metadata["stage_title"], "Call Expert Agent")
        self.assertEqual(progress_events[2].metadata["stage"], "expert_execution")
        self.assertIn("1. List Skills", progress_events[2].text)
        self.assertIn("2. Call Expert Agent", progress_events[2].text)

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

            async def generate_step_plan(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "Done.",
                    "last_response": "",
                    "last_output_message": "",
                    "current_plan": {
                        "next_agent": "FINISH",
                        "parameters": {},
                        "summary": "Done.",
                    },
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

        class _FakeExecutor:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def execute_plan(self):
                raise AssertionError("Executor should not run when the planner finishes directly.")

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator), patch(
            "src.runtime.workflow_service.Executor", _FakeExecutor
        ):
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

            async def generate_step_plan(self) -> dict:
                return {
                    "workflow_status": "finished",
                    "final_summary": "Done.",
                    "last_response": "",
                    "last_output_message": "",
                    "current_plan": {
                        "next_agent": "FINISH",
                        "parameters": {},
                        "summary": "Done.",
                    },
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

        class _FakeExecutor:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def execute_plan(self):
                raise AssertionError("Executor should not run when the planner finishes directly.")

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator), patch(
            "src.runtime.workflow_service.Executor", _FakeExecutor
        ):
            events = [event async for event in runtime.run_message(inbound)]

        progress_events = [event for event in events if event.event_type == "status"]
        self.assertIn("3 entries", progress_events[-1].text)
        self.assertIn("README.md", progress_events[-1].text)

    async def test_run_message_executes_one_expert_via_executor(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="local",
            sender_id="local-user",
            chat_id="terminal",
            text="Analyze the plan before generating the image",
        )

        class _FakeOrchestrator:
            call_count = 0

            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def generate_step_plan(self) -> dict:
                type(self).call_count += 1
                if type(self).call_count == 1:
                    return {
                        "workflow_status": "running",
                        "final_summary": "",
                        "last_response": '{"next_agent":"KnowledgeAgent","parameters":{"topic":"battle"},"summary":"Let the knowledge expert organize the plan first."}',
                        "last_output_message": "",
                        "current_plan": {
                            "next_agent": "KnowledgeAgent",
                            "parameters": {"topic": "battle"},
                            "summary": "Let the knowledge expert organize the plan first.",
                        },
                        "new_orchestration_events": [
                            {
                                "title": "Call Expert Agent",
                                "detail": "Next step will call `KnowledgeAgent`. Goal: Let the knowledge expert organize the plan first.",
                                "stage": "expert_execution",
                            }
                        ],
                    }
                return {
                    "workflow_status": "finished",
                    "final_summary": "The task is complete.",
                    "last_response": '{"next_agent":"FINISH","parameters":{},"summary":"The task is complete."}',
                    "last_output_message": "The task is complete.",
                    "current_plan": {
                        "next_agent": "FINISH",
                        "parameters": {},
                        "summary": "The task is complete.",
                    },
                    "new_orchestration_events": [
                        {
                            "title": "Finalize Result",
                            "detail": "Preparing the final reply.",
                            "stage": "finalizing",
                        }
                    ],
                }

        class _FakeExecutor:
            call_count = 0

            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def execute_plan(self):
                type(self).call_count += 1
                return {"status": "success", "message": "KnowledgeAgent returned the plan."}

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator), patch(
            "src.runtime.workflow_service.Executor", _FakeExecutor
        ):
            events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(_FakeOrchestrator.call_count, 2)
        self.assertEqual(_FakeExecutor.call_count, 1)
        progress_events = [event for event in events if event.event_type == "status"]
        self.assertTrue(any(event.metadata["stage_title"] == "KnowledgeAgent Returned" for event in progress_events))
        self.assertEqual(events[-1].event_type, "final")
        self.assertEqual(events[-1].text, "The task is complete.")

    async def test_run_message_surfaces_round_and_agent_for_executor_failure(self) -> None:
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

            async def generate_step_plan(self) -> dict:
                return {
                    "workflow_status": "running",
                    "final_summary": "",
                    "last_response": "",
                    "last_output_message": "",
                    "current_plan": {
                        "next_agent": "ImageUnderstandingAgent",
                        "parameters": {"input_path": "inbox/demo.png", "mode": "description"},
                        "summary": "Describe this image.",
                    },
                    "new_orchestration_events": [
                        {
                            "title": "Call Expert Agent",
                            "detail": "Next step will call `ImageUnderstandingAgent`. Goal: Describe this image.",
                            "stage": "expert_execution",
                        }
                    ],
                }

        class _FakeExecutor:
            def __init__(self, **_kwargs) -> None:
                self.uid = ""
                self.sid = ""

            async def execute_plan(self):
                raise KeyError("error")

        with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator), patch(
            "src.runtime.workflow_service.Executor", _FakeExecutor
        ):
            events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(events[-1].event_type, "error")
        self.assertIn("Workflow failed", events[-1].text)
        self.assertIn("round=1", events[-1].text)
        self.assertIn("next_agent=ImageUnderstandingAgent", events[-1].text)
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

                async def generate_step_plan(self) -> dict:
                    return {
                        "workflow_status": "finished",
                        "final_summary": "Image description completed.",
                        "last_response": '{"next_agent":"FINISH","parameters":{},"summary":"Image description completed."}',
                        "last_output_message": "Image description completed.",
                        "current_plan": {
                            "next_agent": "FINISH",
                            "parameters": {},
                            "summary": "Image description completed.",
                        },
                    }

            class _FakeExecutor:
                def __init__(self, **_kwargs) -> None:
                    self.uid = ""
                    self.sid = ""

                async def execute_plan(self):
                    raise AssertionError("Executor should not run when the planner finishes directly.")

            with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator), patch(
                "src.runtime.workflow_service.Executor", _FakeExecutor
            ):
                events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(events[-1].event_type, "final")
        self.assertEqual(events[-1].artifact_paths, [])


if __name__ == "__main__":
    unittest.main()
