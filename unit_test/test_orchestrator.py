import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService
from google.adk.sessions.state import State
from google.genai.types import Content

from conf.system import SYS_CONFIG
from src.agents.experts.image_grounding.image_grounding_agent import ImageGroundingAgent
from src.agents.orchestrator.final_response import (
    ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY,
    OrchestratorFinalResponse,
)
from src.agents.orchestrator.orchestrator_agent import (
    Orchestrator,
    _normalize_final_response_paths,
    orchestrator_before_model_callback,
)
from src.runtime.adk_compat import annotate_agent_origin
from src.runtime.workspace import build_workspace_file_record, workspace_relative_path, workspace_root


class OrchestratorTests(unittest.TestCase):
    def test_instruction_mentions_structured_final_response_contract(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )

        instruction = orchestrator._build_instruction()

        self.assertIn("Do not create a full upfront plan", instruction)
        self.assertIn("list_skills", instruction)
        self.assertIn("read_skill", instruction)
        self.assertIn("web_fetch", instruction)
        self.assertIn("web_search", instruction)
        self.assertIn("run_design_production", instruction)
        self.assertIn("run_ppt_production", instruction)
        self.assertIn("deck_spec_review", instruction)
        self.assertIn("run_short_video_production", instruction)
        self.assertIn("image_crop", instruction)
        self.assertIn("image_rotate", instruction)
        self.assertIn("image_flip", instruction)
        self.assertIn("image_info", instruction)
        self.assertIn("video_info", instruction)
        self.assertIn("audio_info", instruction)
        self.assertIn("glob", instruction)
        self.assertIn("grep", instruction)
        self.assertIn("exec_command", instruction)
        self.assertIn("process_session", instruction)
        self.assertIn("invoke_agent(agent_name, prompt)", instruction)
        self.assertIn("Do not output internal workflow JSON", instruction)
        self.assertIn("keep changes small and reviewable", instruction.lower())
        self.assertIn("re-check the latest state", instruction.lower())
        self.assertIn("main conversational agent", instruction.lower())
        self.assertIn("coding, debugging, and file-editing tasks", instruction.lower())
        self.assertIn("background=true", instruction)
        self.assertIn("aspect_ratio", instruction)
        self.assertIn("resolution", instruction)
        self.assertIn("duration_seconds", instruction)
        self.assertIn("video_extension", instruction)
        self.assertIn("native dialogue/audio", instruction)
        self.assertIn("native audio", instruction)
        self.assertIn("SRT/VTT", instruction)
        self.assertIn("SpeechRecognitionExpert", instruction)
        self.assertIn("seedance", instruction)
        self.assertIn("generate_audio=true", instruction)
        self.assertIn("prompt_rewrite=\"off\"", instruction)
        self.assertIn("kling-v1-6", instruction)
        self.assertIn("nano_banana", instruction)
        self.assertIn("seedream", instruction)
        self.assertIn("<skills>", instruction)
        self.assertIn("planning-with-files", instruction)
        self.assertIn("workspace file history", instruction)
        self.assertIn("input_path", instruction)
        self.assertIn("`input_name` is legacy", instruction)
        self.assertIn("list_session_files(section=...)", instruction)
        self.assertIn("reply_text", instruction)
        self.assertIn("final_file_paths", instruction)
        self.assertIn("The final structured response is the only final delivery", instruction)
        self.assertNotIn("message(content=...)", instruction)
        self.assertNotIn("message_file(paths=..., caption=...)", instruction)
        self.assertNotIn("message_image(paths=..., caption=...)", instruction)
        self.assertNotIn("set_final_files(paths=[...])", instruction)
        self.assertIn("aligned with the user's language", instruction)
        self.assertIn("If the user mixes languages", instruction)
        self.assertIn("delivery channel context", instruction)
        self.assertIn("Do not expose raw routing identifiers", instruction)
        self.assertIn("Expert parameter contracts", instruction)
        self.assertIn("SearchAgent: required=query, mode", instruction)
        self.assertIn("plain_prompt=yes", instruction)
        self.assertIn("ImageEditingAgent: required=prompt, input_path or input_paths", instruction)
        self.assertIn("plain_prompt=no", instruction)
        self.assertIn("text prompts only", instruction)
        self.assertIn("modify one or more existing workspace images", instruction)
        self.assertIn("Use mode `prompt`", instruction)
        self.assertIn("save a binary mask image file", instruction)
        self.assertIn("return bounding boxes", instruction)
        self.assertIn("deterministic local image operations", instruction)
        self.assertIn("Serper image search", instruction)
        self.assertIn("Use this expert for text-to-video", instruction)
        self.assertIn("ImageBasicOperations", instruction)
        self.assertIn("VideoBasicOperations", instruction)
        self.assertIn("AudioBasicOperations", instruction)
        self.assertIn("deterministic local video operations", instruction)
        self.assertIn("deterministic local audio operations", instruction)
        self.assertIn("default resource id is `seed-tts-2.0`", instruction)
        self.assertIn("voice_name", instruction)
        self.assertIn("code default model is `music-2.5`", instruction)
        self.assertIn("Provider `hy3d` remains the default", instruction)
        self.assertIn("doubao-seed3d-2-0-260328", instruction)
        self.assertIn("hyper3d-gen2-260112", instruction)
        self.assertIn("hitem3d-2-0-251223", instruction)
        self.assertIn("Use `task=subtitle`", instruction)
        self.assertIn("SRT/VTT", instruction)
        self.assertIn("Creative workflow routing hints", instruction)
        self.assertIn("creative-brief-to-storyboard", instruction)
        self.assertIn("narration-to-visual-prompts", instruction)
        self.assertIn("asset-to-script", instruction)
        self.assertIn("style-brief-to-prompt", instruction)
        self.assertIn("creative-workflow-router", instruction)
        self.assertIn("creative-qc", instruction)
        self.assertIn("do not skip straight to `ImageGenerationAgent` or `VideoGenerationAgent`", instruction)
        self.assertIn("PPT production", instruction)
        self.assertIn("brief_review", instruction)
        self.assertIn("outline_review", instruction)
        self.assertIn("deck_spec_review", instruction)
        self.assertIn("page_preview_review", instruction)
        self.assertIn("render manifest", instruction)
        self.assertIn("view_type=\"manifest\"", instruction)
        self.assertIn("slide_number", instruction)
        self.assertIn("target_kind", instruction)
        self.assertIn("target_id", instruction)
        self.assertIn("final_preview_review", instruction)
        self.assertIn('action="regenerate_stale_segments"', instruction)
        self.assertIn("P1e gated shot-segment review", instruction)
        self.assertIn("view_type=\"quality\"", instruction)
        self.assertIn("storyboard_review", instruction)
        self.assertIn("shot_review", instruction)
        self.assertIn("cartoon_short_drama", instruction)
        self.assertIn("social_media_short", instruction)
        self.assertIn("do not recommend unsupported ratios", instruction)
        self.assertIn('action="view"', instruction)
        self.assertIn('action="add_reference_assets"', instruction)
        self.assertIn('action="analyze_revision_impact"', instruction)
        self.assertIn('action="apply_revision"', instruction)
        self.assertIn("needs_user_review", instruction)
        self.assertIn("completed artifacts", instruction)

    def test_agent_uses_structured_output_schema(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )

        self.assertIs(orchestrator.agent.output_schema, OrchestratorFinalResponse)
        self.assertEqual(
            orchestrator.agent.output_key,
            ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY,
        )

    def test_list_skills_records_orchestration_step(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(state={})

        orchestrator.list_skills(tool_context=tool_context)

        events = tool_context.state["orchestration_events"]
        self.assertEqual(events[0]["title"], "List Skills")
        self.assertEqual(events[0]["stage"], "planning")

    def test_read_file_records_orchestration_step(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(state={})

        orchestrator.read_file("README.md", tool_context=tool_context)

        events = tool_context.state["orchestration_events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["title"], "read_file")
        self.assertIn("path=README.md", events[0]["detail"])
        self.assertIn("Status: started", events[0]["detail"])
        self.assertIn("Result:", events[1]["detail"])
        self.assertIn("path=README.md", events[1]["detail"])

    def test_list_session_files_returns_latest_output_records(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(
            state={
                "input_files": [],
                "new_files": [],
                "files_history": [
                    [{"name": "upload.png", "path": "inbox/cli/upload.png", "source": "channel"}],
                    [{"name": "result.png", "path": "generated/session/result.png", "source": "image_grounding"}],
                ],
            }
        )

        result = orchestrator.list_session_files(section="latest_output", tool_context=tool_context)
        payload = json.loads(result)

        self.assertEqual(len(payload["latest_output_files"]), 1)
        self.assertEqual(payload["latest_output_files"][0]["path"], "generated/session/result.png")

    def test_normalize_final_response_paths_accepts_tracked_relative_file(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            file_path = Path(tmpdir) / "result.png"
            file_path.write_bytes(b"fake-image")
            file_record = build_workspace_file_record(
                file_path,
                description="generated image",
                source="image_generation",
            )

            normalized = _normalize_final_response_paths(
                [workspace_relative_path(file_path), workspace_relative_path(file_path)],
                state={"generated": [file_record]},
            )

        self.assertEqual(normalized, [workspace_relative_path(file_path)])

    def test_normalize_final_response_paths_rejects_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            file_path = Path(tmpdir) / "result.png"
            file_path.write_bytes(b"fake-image")
            file_record = build_workspace_file_record(
                file_path,
                description="generated image",
                source="image_generation",
            )

            with self.assertRaisesRegex(ValueError, "workspace-relative"):
                _normalize_final_response_paths(
                    [str(file_path.resolve())],
                    state={"generated": [file_record]},
                )

    def test_normalize_final_response_paths_rejects_untracked_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            file_path = Path(tmpdir) / "result.png"
            file_path.write_bytes(b"fake-image")

            with self.assertRaisesRegex(ValueError, "current session file history"):
                _normalize_final_response_paths(
                    [workspace_relative_path(file_path)],
                    state={"generated": []},
                )

    def test_summarize_read_file_result_prefers_preview(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "read_file",
            "line one\nline two\nline three\nline four",
        )

        self.assertEqual(status, "success")
        self.assertIn("Read succeeded", summary)
        self.assertIn("line one", summary)
        self.assertIn("End:", summary)
        self.assertIn("line four", summary)

    def test_summarize_list_dir_counts_entries(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "list_dir",
            "[D] src\n[F] README.md\n[F] pyproject.toml",
        )

        self.assertEqual(status, "success")
        self.assertIn("3 entries", summary)
        self.assertIn("README.md", summary)

    def test_summarize_exec_command_counts_lines(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "exec_command",
            "total 8\n-rw-r--r-- file.txt\n-rw-r--r-- app.py\nSTDERR:\nwarn one\nwarn two",
        )

        self.assertEqual(status, "success")
        self.assertIn("Command completed", summary)
        self.assertIn("about 3 stdout lines", summary)
        self.assertIn("about 2 stderr lines", summary)
        self.assertIn("stderr summary", summary)

    def test_summarize_background_exec_command_mentions_session(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "exec_command",
            "Command still running (session abc123, pid 456). Use process_session(action='list'|'poll') for follow-up.",
        )

        self.assertEqual(status, "success")
        self.assertIn("Background command started", summary)
        self.assertIn("abc123", summary)

    def test_summarize_glob_result_counts_matches(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "glob",
            "src/app.py\nsrc/nested/worker.py",
        )

        self.assertEqual(status, "success")
        self.assertIn("2 matching paths", summary)
        self.assertIn("src/app.py", summary)

    def test_summarize_process_session_result_mentions_status(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "process_session",
            "build finished\n\nStatus: exited\nExit code: 0",
        )

        self.assertEqual(status, "success")
        self.assertIn("Session update received", summary)
        self.assertIn("exited", summary)

    def test_summarize_web_fetch_uses_json_fields(self) -> None:
        payload = (
            "{"
            '"url":"https://example.com",'
            '"finalUrl":"https://example.com",'
            '"status":200,'
            '"extractor":"html",'
            '"truncated":false,'
            '"length":42,'
            '"text":"alpha\\nbeta\\ngamma\\ndelta"'
            "}"
        )
        status, summary = Orchestrator._summarize_tool_result("web_fetch", payload)

        self.assertEqual(status, "success")
        self.assertIn("extractor=html", summary)
        self.assertIn("alpha", summary)
        self.assertIn("End:", summary)
        self.assertIn("delta", summary)

    def test_summarize_invoke_agent_result_uses_structured_fields(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "invoke_agent",
            {
                "agent_name": "KnowledgeAgent",
                "status": "success",
                "message": "analysis complete",
                "output_text": "line one\nline two",
                "output_files": [{"path": "generated/demo.txt"}],
            },
        )

        self.assertEqual(status, "success")
        self.assertIn("KnowledgeAgent finished", summary)
        self.assertIn("files=1", summary)
        self.assertIn("analysis complete", summary)

    def test_summarize_invoke_agent_error_marks_failure(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "invoke_agent",
            {
                "agent_name": "SearchAgent",
                "status": "error",
                "message": "search failed",
            },
        )

        self.assertEqual(status, "error")
        self.assertIn("search failed", summary)

    def test_summarize_list_session_files_result_uses_latest_output_summary(self) -> None:
        payload = json.dumps(
            {
                "latest_output_files": [
                    {"path": "generated/session/a.png"},
                    {"path": "generated/session/b.png"},
                ]
            },
            ensure_ascii=False,
        )
        status, summary = Orchestrator._summarize_tool_result("list_session_files", payload)

        self.assertEqual(status, "success")
        self.assertIn("latest_output_files contains 2 record(s)", summary)
        self.assertIn("generated/session/a.png", summary)


class OrchestratorCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_before_model_callback_includes_workspace_file_history_without_new_upload(self) -> None:
        callback_context = SimpleNamespace(
            state={
                "workflow_status": "running",
                "step": 2,
                "user_prompt": "Flip this image upside down for me.",
                "channel": "cli",
                "chat_id": "terminal",
                "sender_id": "cli-user",
                "input_files": [],
                "summary_history": ["Call `ImageGenerationAgent` to generate an image."],
                "message_history": [
                    "ImageGenerationAgent has completed 1 image generation tasks: "
                    "image generation task1 success, output file: step1_generation_output0.png"
                ],
                "files_history": [
                    [
                        {
                            "name": "step1_generation_output0.png",
                            "path": "generated/session_1/step1_generation_output0.png",
                            "description": "generated image from previous step",
                        }
                    ]
                ],
                "new_files": [],
            }
        )
        llm_request = SimpleNamespace(contents=[])

        await orchestrator_before_model_callback(callback_context, llm_request)

        self.assertEqual(len(llm_request.contents), 1)
        self.assertIsInstance(llm_request.contents[0], Content)
        prompt_text = "\n".join(
            part.text for part in llm_request.contents[0].parts if getattr(part, "text", None)
        )
        self.assertIn("step1_generation_output0.png", prompt_text)
        self.assertIn("Most recent available output files", prompt_text)
        self.assertIn("Delivery context: channel=cli; chat_id=terminal; sender_id=cli-user", prompt_text)
        self.assertIn("Final response contract", prompt_text)
        self.assertIn("reply_text", prompt_text)
        self.assertIn("final_file_paths", prompt_text)
        self.assertIn("list_session_files(section=\"latest_output\")", prompt_text)

    async def test_run_until_done_uses_structured_final_response(self) -> None:
        session_service = InMemorySessionService()
        artifact_service = InMemoryArtifactService()
        orchestrator = Orchestrator(
            session_service=session_service,
            artifact_service=artifact_service,
            expert_agents={},
        )
        orchestrator.uid = "user-1"
        orchestrator.sid = "session-1"

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            file_path = Path(tmpdir) / "result.png"
            file_path.write_bytes(b"fake-image")
            file_record = build_workspace_file_record(
                file_path,
                description="generated image",
                source="image_generation",
            )
            relative_path = workspace_relative_path(file_path)

            await session_service.create_session(
                app_name=SYS_CONFIG.app_name,
                user_id=orchestrator.uid,
                session_id=orchestrator.sid,
                state={
                    "orchestration_events": [],
                    "generated": [file_record],
                    "generated_history": [],
                    "uploaded": [],
                    "uploaded_history": [],
                    "files_history": [[file_record]],
                    ORCHESTRATOR_FINAL_RESPONSE_OUTPUT_KEY: {
                        "reply_text": "Here is the final image.",
                        "final_file_paths": [relative_path],
                    },
                },
            )

            with patch.object(
                orchestrator,
                "run_agent_and_log_events",
                new=AsyncMock(return_value="raw final text"),
            ):
                result = await orchestrator.run_until_done()

        self.assertEqual(result["final_response"], "Here is the final image.")
        self.assertEqual(result["final_file_paths"], [relative_path])

        session = await session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=orchestrator.uid,
            session_id=orchestrator.sid,
        )
        self.assertEqual(session.state["final_response"], "Here is the final image.")
        self.assertEqual(session.state["final_file_paths"], [relative_path])

    async def test_run_until_done_requires_structured_final_response(self) -> None:
        session_service = InMemorySessionService()
        artifact_service = InMemoryArtifactService()
        orchestrator = Orchestrator(
            session_service=session_service,
            artifact_service=artifact_service,
            expert_agents={},
        )
        orchestrator.uid = "user-1"
        orchestrator.sid = "session-1"

        await session_service.create_session(
            app_name=SYS_CONFIG.app_name,
            user_id=orchestrator.uid,
            session_id=orchestrator.sid,
            state={"orchestration_events": []},
        )

        with patch.object(
            orchestrator,
            "run_agent_and_log_events",
            new=AsyncMock(return_value="fallback final text"),
        ):
            with self.assertRaisesRegex(ValueError, "Missing structured final response"):
                await orchestrator.run_until_done()


class OrchestratorInvokeAgentIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_invoke_agent_runs_real_grounding_expert_through_dispatcher(self) -> None:
        expert_origin_path = Path(__file__).resolve().parents[1] / "src" / "agents"
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={
                "ImageGroundingAgent": annotate_agent_origin(
                    ImageGroundingAgent(name="ImageGroundingAgent"),
                    app_name=SYS_CONFIG.app_name,
                    origin_path=expert_origin_path,
                )
            },
        )
        tool_context = SimpleNamespace(
            state=State(
                {
                    "step": 0,
                    "files_history": [],
                    "summary_history": [],
                    "text_history": [],
                    "message_history": [],
                    "expert_history": [],
                },
                {},
            ),
            _invocation_context=SimpleNamespace(user_id="user-1"),
        )
        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            image_path = Path(tmpdir) / "grounding_input.png"
            image_path.write_bytes(b"fake-image")
            relative_image_path = workspace_relative_path(image_path)

            with patch(
                "src.agents.experts.image_grounding.image_grounding_agent.dino_xseek_detection_tool",
                new=AsyncMock(
                    return_value={
                        "status": "success",
                        "message": "Detected 1 object.",
                        "input_path": relative_image_path,
                        "prompt": "cat",
                        "objects": [{"bbox": [1.0, 2.0, 3.0, 4.0]}],
                        "bboxes": [[1.0, 2.0, 3.0, 4.0]],
                        "task_uuid": "task-1",
                        "session_id": "child-session",
                        "provider": "deepdataspace",
                        "model_name": "DINO-XSeek-1.0",
                    }
                ),
            ):
                result = await orchestrator.invoke_agent(
                    "ImageGroundingAgent",
                    f'{{"input_path":"{relative_image_path}","prompt":"cat"}}',
                    tool_context=tool_context,
                )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["agent_name"], "ImageGroundingAgent")
        self.assertIn("image_ground_results", result["structured_data"])
        self.assertEqual(
            result["structured_data"]["image_ground_results"][0]["bboxes"][0],
            [1.0, 2.0, 3.0, 4.0],
        )
        self.assertEqual(tool_context.state["step"], 1)
        self.assertEqual(tool_context.state["current_output"]["status"], "success")
        self.assertEqual(tool_context.state["expert_history"][-1]["agent_name"], "ImageGroundingAgent")
        self.assertEqual(tool_context.state["orchestration_events"][0]["title"], "invoke_agent")
        self.assertIn("agent_name=ImageGroundingAgent", tool_context.state["orchestration_events"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
