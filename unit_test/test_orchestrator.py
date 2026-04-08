import unittest

from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService

from src.agents.orchestrator.orchestrator_agent import Orchestrator


class OrchestratorTests(unittest.TestCase):
    def test_instruction_mentions_skill_workflow_and_no_global_plan(self) -> None:
        orchestrator = Orchestrator(
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            expert_runners={},
        )

        instruction = orchestrator._build_instruction()

        self.assertIn("Do not create a full upfront plan", instruction)
        self.assertIn("list_skills", instruction)
        self.assertIn("read_skill", instruction)
        self.assertIn("run_expert", instruction)
        self.assertIn("web_fetch", instruction)
        self.assertIn("web_search", instruction)
        self.assertIn("reverse prompt extraction", instruction)
        self.assertIn("aspect_ratio", instruction)
        self.assertIn("resolution", instruction)
        self.assertIn("nano_banana", instruction)
        self.assertIn("seedream", instruction)
        self.assertIn("<skills>", instruction)
        self.assertIn("planning-with-files", instruction)


if __name__ == "__main__":
    unittest.main()
