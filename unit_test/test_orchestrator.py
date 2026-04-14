import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService
from google.adk.sessions.state import State
from google.genai.types import Content

from conf.system import SYS_CONFIG
from src.agents.experts.image_grounding.image_grounding_agent import ImageGroundingAgent
from src.agents.orchestrator.orchestrator_agent import Orchestrator, orchestrator_before_model_callback
from src.runtime.adk_compat import annotate_agent_origin
from src.runtime.workspace import workspace_relative_path, workspace_root


class OrchestratorTests(unittest.TestCase):
    def test_instruction_mentions_skill_workflow_and_invoke_agent_path(self) -> None:
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
        self.assertIn("ImageToPromptAgent", instruction)
        self.assertIn("aspect_ratio", instruction)
        self.assertIn("resolution", instruction)
        self.assertIn("nano_banana", instruction)
        self.assertIn("seedream", instruction)
        self.assertIn("<skills>", instruction)
        self.assertIn("planning-with-files", instruction)
        self.assertIn("workspace file history", instruction)
        self.assertIn("input_path", instruction)
        self.assertIn("`input_name` is legacy", instruction)
        self.assertIn("list_session_files(section=...)", instruction)
        self.assertIn("set_final_files(paths=[...])", instruction)
        self.assertIn("runtime will attach the selected workspace files", instruction)
        self.assertIn("aligned with the user's language", instruction)
        self.assertIn("If the user mixes languages", instruction)
        self.assertIn("Expert parameter contracts", instruction)
        self.assertIn("SearchAgent: required=query, mode", instruction)
        self.assertIn("plain_prompt=yes", instruction)
        self.assertIn("ImageEditingAgent: required=prompt, input_path or input_paths", instruction)
        self.assertIn("plain_prompt=no", instruction)
        self.assertIn("ImageBasicOperations", instruction)
        self.assertIn("VideoBasicOperations", instruction)
        self.assertIn("AudioBasicOperations", instruction)
        self.assertIn("Creative workflow routing hints", instruction)
        self.assertIn("creative-brief-to-storyboard", instruction)
        self.assertIn("narration-to-visual-prompts", instruction)
        self.assertIn("asset-to-script", instruction)
        self.assertIn("style-brief-to-prompt", instruction)
        self.assertIn("creative-workflow-router", instruction)
        self.assertIn("creative-qc", instruction)
        self.assertIn("do not skip straight to `ImageGenerationAgent` or `VideoGenerationAgent`", instruction)

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

    def test_set_final_files_updates_state_with_workspace_relative_paths(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(state={})

        with tempfile.TemporaryDirectory(dir=workspace_root()) as tmpdir:
            file_path = Path(tmpdir) / "person_bbox_marked.png"
            file_path.write_bytes(b"fake-image")
            relative_path = workspace_relative_path(file_path)

            result = orchestrator.set_final_files([relative_path], tool_context=tool_context)
            self.assertIn("Selected 1 final file", result)
            self.assertEqual(tool_context.state["final_file_paths"], [relative_path])

            cleared = orchestrator.set_final_files([], tool_context=tool_context)
            self.assertIn("Cleared the final file selection", cleared)
            self.assertEqual(tool_context.state["final_file_paths"], [])

    def test_set_final_files_rejects_paths_outside_workspace(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_agents={},
        )
        tool_context = SimpleNamespace(state={})

        with tempfile.TemporaryDirectory() as tmpdir:
            outside_path = Path(tmpdir) / "external.png"
            outside_path.write_bytes(b"external")

            result = orchestrator.set_final_files([str(outside_path)], tool_context=tool_context)

        self.assertTrue(result.startswith("Error: Invalid workspace path"))
        self.assertNotIn("final_file_paths", tool_context.state)

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
                "final_file_paths": None,
            }
        )

        result = orchestrator.list_session_files(section="latest_output", tool_context=tool_context)
        payload = json.loads(result)

        self.assertEqual(len(payload["latest_output_files"]), 1)
        self.assertEqual(payload["latest_output_files"][0]["path"], "generated/session/result.png")

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
            '{'
            '"url":"https://example.com",'
            '"finalUrl":"https://example.com",'
            '"status":200,'
            '"extractor":"html",'
            '"truncated":false,'
            '"length":42,'
            '"text":"alpha\\nbeta\\ngamma\\ndelta"'
            '}'
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

    def test_summarize_list_session_files_result_uses_final_selection_summary(self) -> None:
        payload = json.dumps(
            {"final_file_paths": ["generated/session/a.png", "generated/session/b.png"]},
            ensure_ascii=False,
        )
        status, summary = Orchestrator._summarize_tool_result("list_session_files", payload)

        self.assertEqual(status, "success")
        self.assertIn("Final file selection contains 2 path(s)", summary)
        self.assertIn("generated/session/a.png", summary)

class OrchestratorCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_before_model_callback_includes_workspace_file_history_without_new_upload(self) -> None:
        callback_context = SimpleNamespace(
            state={
                "workflow_status": "running",
                "step": 2,
                "user_prompt": "Flip this image upside down for me.",
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

    async def test_before_model_callback_includes_explicit_final_file_selection(self) -> None:
        callback_context = SimpleNamespace(
            state={
                "workflow_status": "running",
                "step": 3,
                "user_prompt": "Send the marked image back to me.",
                "input_files": [],
                "summary_history": [],
                "message_history": [],
                "files_history": [],
                "new_files": [],
                "final_file_paths": ["generated/session_1/person_bbox_marked.png"],
            }
        )
        llm_request = SimpleNamespace(contents=[])

        await orchestrator_before_model_callback(callback_context, llm_request)

        prompt_text = "\n".join(
            part.text for part in llm_request.contents[0].parts if getattr(part, "text", None)
        )
        self.assertIn("Explicitly selected final reply files", prompt_text)
        self.assertIn("generated/session_1/person_bbox_marked.png", prompt_text)

    async def test_before_model_callback_marks_explicitly_cleared_final_files(self) -> None:
        callback_context = SimpleNamespace(
            state={
                "workflow_status": "running",
                "step": 1,
                "user_prompt": "Reply with text only.",
                "input_files": [],
                "summary_history": [],
                "message_history": [],
                "files_history": [],
                "new_files": [],
                "final_file_paths": [],
            }
        )
        llm_request = SimpleNamespace(contents=[])

        await orchestrator_before_model_callback(callback_context, llm_request)

        prompt_text = "\n".join(
            part.text for part in llm_request.contents[0].parts if getattr(part, "text", None)
        )
        self.assertIn("attachments have been explicitly cleared", prompt_text)


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
