import unittest
from types import SimpleNamespace

from google.adk.artifacts import InMemoryArtifactService
from google.genai.types import Content
from google.adk.sessions import InMemorySessionService

from src.agents.orchestrator.orchestrator_agent import Orchestrator, orchestrator_before_model_callback


class OrchestratorTests(unittest.TestCase):
    def test_instruction_mentions_skill_workflow_and_single_step_json_plan(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_runners={},
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
        self.assertIn("exactly one JSON object", instruction)
        self.assertIn('"next_agent"', instruction)
        self.assertIn("cannot execute them directly", instruction)
        self.assertIn("keep changes small and reviewable", instruction.lower())
        self.assertIn("re-check the latest state", instruction.lower())
        self.assertIn("ImageToPromptAgent", instruction)
        self.assertIn("aspect_ratio", instruction)
        self.assertIn("resolution", instruction)
        self.assertIn("nano_banana", instruction)
        self.assertIn("seedream", instruction)
        self.assertIn("<skills>", instruction)
        self.assertIn("planning-with-files", instruction)
        self.assertIn("workspace file history", instruction)
        self.assertIn("input_path", instruction)

    def test_normalize_step_plan_accepts_known_expert(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_runners={"KnowledgeAgent": object()},
        )

        plan = orchestrator._normalize_step_plan(
            {
                "next_agent": "KnowledgeAgent",
                "parameters": {"topic": "desert"},
                "summary": "先让知识专家整理方案。",
            }
        )

        self.assertEqual(plan["next_agent"], "KnowledgeAgent")
        self.assertEqual(plan["parameters"], {"topic": "desert"})
        self.assertEqual(plan["summary"], "先让知识专家整理方案。")

    def test_normalize_step_plan_maps_null_like_values_to_finish(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_runners={},
        )

        plan = orchestrator._normalize_step_plan(
            {
                "next_agent": "null",
                "parameters": {},
                "summary": "",
            }
        )

        self.assertEqual(plan["next_agent"], "FINISH")
        self.assertEqual(plan["parameters"], {})
        self.assertIn("完成", plan["summary"])

    def test_list_skills_records_orchestration_step(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_runners={},
        )
        tool_context = SimpleNamespace(state={})

        orchestrator.list_skills(tool_context=tool_context)

        events = tool_context.state["orchestration_events"]
        self.assertEqual(events[0]["title"], "查看技能列表")
        self.assertEqual(events[0]["stage"], "planning")

    def test_read_file_records_orchestration_step(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_runners={},
        )
        tool_context = SimpleNamespace(state={})

        orchestrator.read_file("README.md", tool_context=tool_context)

        events = tool_context.state["orchestration_events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["title"], "read_file")
        self.assertIn("path=README.md", events[0]["detail"])
        self.assertIn("状态：开始", events[0]["detail"])
        self.assertIn("结果：", events[1]["detail"])
        self.assertIn("path=README.md", events[1]["detail"])

    def test_summarize_read_file_result_prefers_preview(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "read_file",
            "line one\nline two\nline three\nline four",
        )

        self.assertEqual(status, "success")
        self.assertIn("读取成功", summary)
        self.assertIn("line one", summary)
        self.assertIn("结尾", summary)
        self.assertIn("line four", summary)

    def test_summarize_list_dir_counts_entries(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "list_dir",
            "[D] src\n[F] README.md\n[F] pyproject.toml",
        )

        self.assertEqual(status, "success")
        self.assertIn("共 3 个条目", summary)
        self.assertIn("README.md", summary)

    def test_summarize_exec_command_counts_lines(self) -> None:
        status, summary = Orchestrator._summarize_tool_result(
            "exec_command",
            "total 8\n-rw-r--r-- file.txt\n-rw-r--r-- app.py\nSTDERR:\nwarn one\nwarn two",
        )

        self.assertEqual(status, "success")
        self.assertIn("命令执行完成", summary)
        self.assertIn("stdout 约 3 行", summary)
        self.assertIn("stderr 约 2 行", summary)
        self.assertIn("stderr 摘要", summary)

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
        self.assertIn("结尾", summary)
        self.assertIn("delta", summary)

class OrchestratorCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_before_model_callback_includes_workspace_file_history_without_new_upload(self) -> None:
        callback_context = SimpleNamespace(
            state={
                "workflow_status": "running",
                "step": 2,
                "user_prompt": "把这个图像上下颠倒一下给我",
                "input_files": [],
                "summary_history": ["调用 `ImageGenerationAgent` 生成图片"],
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
