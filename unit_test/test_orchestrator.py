import unittest
from types import SimpleNamespace

from google.adk.artifacts import InMemoryArtifactService
from google.genai.types import Content
from google.adk.sessions import InMemorySessionService

from src.agents.orchestrator.orchestrator_agent import Orchestrator, orchestrator_before_model_callback


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
        self.assertIn("aligned with the user's language", instruction)
        self.assertIn("If the user mixes languages", instruction)
        self.assertIn("Expert parameter contracts", instruction)
        self.assertIn("SearchAgent: required=query, mode", instruction)
        self.assertIn("plain_prompt=yes", instruction)
        self.assertIn("ImageEditingAgent: required=prompt, input_path or input_paths", instruction)
        self.assertIn("plain_prompt=no", instruction)

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


if __name__ == "__main__":
    unittest.main()
