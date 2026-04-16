import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from google.adk.events import Event, EventActions
from google.adk.runners import Runner

from conf.system import SYS_CONFIG
from src.runtime.models import InboundMessage, MessageAttachment
from src.runtime.workflow_service import CreativeClawRuntime
from src.runtime.workspace import build_workspace_file_record, workspace_relative_path, workspace_root


class RuntimeSessionTests(unittest.IsolatedAsyncioTestCase):
    def test_runtime_registers_image_understanding_expert(self) -> None:
        runtime = CreativeClawRuntime()
        image_understanding_agent = runtime.expert_agents["ImageUnderstandingAgent"]

        self.assertIn("ImageUnderstandingAgent", runtime.expert_agents)
        self.assertEqual(
            getattr(image_understanding_agent, "_adk_origin_app_name", None),
            SYS_CONFIG.app_name,
        )
        self.assertIsNotNone(getattr(image_understanding_agent, "_adk_origin_path", None))

    def test_runtime_registers_image_segmentation_expert(self) -> None:
        runtime = CreativeClawRuntime()
        segmentation_agent = runtime.expert_agents["ImageSegmentationAgent"]

        self.assertIn("ImageSegmentationAgent", runtime.expert_agents)
        self.assertEqual(
            getattr(segmentation_agent, "_adk_origin_app_name", None),
            SYS_CONFIG.app_name,
        )
        self.assertIsNotNone(getattr(segmentation_agent, "_adk_origin_path", None))

    def test_runtime_registers_video_generation_expert(self) -> None:
        runtime = CreativeClawRuntime()
        video_agent = runtime.expert_agents["VideoGenerationAgent"]

        self.assertIn("VideoGenerationAgent", runtime.expert_agents)
        self.assertEqual(
            getattr(video_agent, "_adk_origin_app_name", None),
            SYS_CONFIG.app_name,
        )
        self.assertIsNotNone(getattr(video_agent, "_adk_origin_path", None))

    def test_runtime_registers_3d_generation_expert(self) -> None:
        runtime = CreativeClawRuntime()
        three_d_agent = runtime.expert_agents["3DGeneration"]

        self.assertIn("3DGeneration", runtime.expert_agents)
        self.assertEqual(
            getattr(three_d_agent, "_adk_origin_app_name", None),
            SYS_CONFIG.app_name,
        )
        self.assertIsNotNone(getattr(three_d_agent, "_adk_origin_path", None))

    def test_runtime_registers_new_understanding_and_transform_experts(self) -> None:
        runtime = CreativeClawRuntime()

        for expert_name in (
            "TextTransformExpert",
            "VideoUnderstandingExpert",
            "SpeechRecognitionExpert",
            "SpeechTranscriptionExpert",
            "SpeechSynthesisExpert",
            "MusicGenerationExpert",
        ):
            self.assertIn(expert_name, runtime.expert_agents)
            self.assertEqual(
                getattr(runtime.expert_agents[expert_name], "_adk_origin_app_name", None),
                SYS_CONFIG.app_name,
            )
            self.assertIsNotNone(getattr(runtime.expert_agents[expert_name], "_adk_origin_path", None))

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
            channel="cli",
            sender_id="cli-user",
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
            channel="cli",
            sender_id="cli-user",
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
            channel="cli",
            sender_id="cli-user",
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
            channel="cli",
            sender_id="cli-user",
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
        self.assertIsNone(session.state["final_file_paths"])
        self.assertFalse(session.state["direct_outbound_sent"])

    async def test_initial_state_persists_uploaded_files_in_history(self) -> None:
        runtime = CreativeClawRuntime()
        with tempfile.TemporaryDirectory() as tmpdir:
            upload_path = Path(tmpdir) / "demo.png"
            upload_path.write_bytes(b"fake-image")
            inbound = InboundMessage(
                channel="cli",
                sender_id="cli-user",
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
        self.assertTrue(session.state["input_files"][0]["path"].startswith("inbox/cli/"))

    async def test_run_message_uses_natural_progress_messages(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
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
            channel="cli",
            sender_id="cli-user",
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
            channel="cli",
            sender_id="cli-user",
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
            channel="cli",
            sender_id="cli-user",
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
            channel="cli",
            sender_id="cli-user",
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
                        "text_history": ["This is a long expert output."],
                        "summary_history": [],
                        "final_summary": "Internal completion summary.",
                        "final_response": "This is the final reply shown to the user.",
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
        self.assertEqual(final_event.text, "This is the final reply shown to the user.")

    async def test_build_final_event_prefers_explicit_final_file_paths_over_latest_outputs(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="send this exact file",
        )

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            selected_file = Path(tmpdir) / "selected.png"
            fallback_file = Path(tmpdir) / "fallback.png"
            selected_file.write_bytes(b"selected")
            fallback_file.write_bytes(b"fallback")

            selected_relative = workspace_relative_path(selected_file)
            fallback_record = build_workspace_file_record(
                fallback_file,
                description="fallback file",
                source="image_generation",
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
                            "files_history": [[fallback_record]],
                            "final_file_paths": [selected_relative],
                            "final_response": "Sent the selected file.",
                        }
                    ),
                ),
            )

            final_event = await runtime._build_final_event(
                user_id=user_id,
                session_id=session_id,
                final_summary="fallback reply",
            )

        self.assertEqual(final_event.artifact_paths, [str(selected_file.resolve())])
        self.assertEqual(final_event.text, "Sent the selected file.")

    async def test_build_final_event_does_not_replay_latest_generated_outputs_when_selection_is_unset(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="which model generated that video",
        )

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            generated_file = Path(tmpdir) / "latest.mp4"
            generated_file.write_bytes(b"video")
            generated_record = build_workspace_file_record(
                generated_file,
                description="generated video",
                source="video_generation",
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
                            "files_history": [[generated_record]],
                            "final_file_paths": None,
                            "final_response": "It used the Veo provider.",
                            "direct_outbound_sent": False,
                        }
                    ),
                ),
            )

            final_event = await runtime._build_final_event(
                user_id=user_id,
                session_id=session_id,
                final_summary="fallback reply",
            )

        self.assertEqual(final_event.artifact_paths, [])
        self.assertEqual(final_event.text, "It used the Veo provider.")

    async def test_build_final_event_respects_explicit_empty_final_file_selection(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="reply without attachments",
        )

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            fallback_file = Path(tmpdir) / "fallback.png"
            fallback_file.write_bytes(b"fallback")
            fallback_record = build_workspace_file_record(
                fallback_file,
                description="fallback file",
                source="image_generation",
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
                            "files_history": [[fallback_record]],
                            "final_file_paths": [],
                            "final_response": "Return text only.",
                        }
                    ),
                ),
            )

            final_event = await runtime._build_final_event(
                user_id=user_id,
                session_id=session_id,
                final_summary="fallback reply",
            )

        self.assertEqual(final_event.artifact_paths, [])
        self.assertEqual(final_event.text, "Return text only.")

    async def test_build_final_event_hides_runtime_final_when_explicit_outbound_was_sent(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="send me the result",
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
                        "direct_outbound_sent": True,
                        "final_response": "This text should stay hidden.",
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
        self.assertEqual(final_event.text, "")
        self.assertFalse(final_event.metadata["visible"])

    async def test_run_message_surfaces_orchestrator_failure(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
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
                channel="cli",
                sender_id="cli-user",
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

    async def test_run_message_follow_up_does_not_replay_previous_generated_video(self) -> None:
        runtime = CreativeClawRuntime()
        inbound = InboundMessage(
            channel="cli",
            sender_id="cli-user",
            chat_id="terminal",
            text="which model generated that video",
        )

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            generated_file = Path(tmpdir) / "generated_video.mp4"
            generated_file.write_bytes(b"video")
            generated_record = build_workspace_file_record(
                generated_file,
                description="previous generated video",
                source="video_generation",
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
                            "files_history": [[generated_record]],
                            "summary_history": [],
                            "message_history": [],
                            "text_history": [],
                            "final_file_paths": None,
                            "direct_outbound_sent": False,
                        }
                    ),
                ),
            )

            class _FakeOrchestrator:
                def __init__(self, **_kwargs) -> None:
                    self.uid = ""
                    self.sid = ""

                async def run_until_done(self) -> dict:
                    return {
                        "workflow_status": "finished",
                        "final_summary": "It was generated with Veo.",
                        "final_response": "It was generated with Veo.",
                        "last_output_message": "It was generated with Veo.",
                        "new_orchestration_events": [],
                    }

            with patch("src.runtime.workflow_service.Orchestrator", _FakeOrchestrator):
                events = [event async for event in runtime.run_message(inbound)]

        self.assertEqual(events[-1].event_type, "final")
        self.assertEqual(events[-1].text, "It was generated with Veo.")
        self.assertEqual(events[-1].artifact_paths, [])


if __name__ == "__main__":
    unittest.main()
