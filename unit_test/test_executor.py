import shutil
import unittest
import uuid
from pathlib import Path

from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService

from conf.system import SYS_CONFIG
from src.agents.executor.executor_agent import Executor
from src.runtime.workspace import workspace_root


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_add_event_accepts_text_without_state_delta(self) -> None:
        session_service = InMemorySessionService()
        artifact_service = InMemoryArtifactService()
        executor = Executor(
            session_service=session_service,
            artifact_service=artifact_service,
            expert_runners={},
        )
        executor.uid = "user_1"
        executor.sid = "session_1"
        await session_service.create_session(
            app_name=SYS_CONFIG.app_name,
            user_id=executor.uid,
            session_id=executor.sid,
            state={},
        )

        await executor.add_event(text="plain error text")

        session = await session_service.get_session(
            app_name=SYS_CONFIG.app_name,
            user_id=executor.uid,
            session_id=executor.sid,
        )
        self.assertIsNotNone(session)

    def test_normalize_input_paths_accepts_path_like_legacy_input_name(self) -> None:
        test_dir = workspace_root() / "generated" / f"executor_test_{uuid.uuid4().hex[:8]}"
        test_dir.mkdir(parents=True, exist_ok=True)
        try:
            image_path = test_dir / "sample.png"
            image_path.write_bytes(b"png-data")
            relative_path = str(image_path.relative_to(workspace_root()))

            normalized = Executor._normalize_input_paths(
                state={"input_files": [], "files_history": []},
                parameters={"input_name": relative_path, "mode": "description"},
            )
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)

        self.assertNotIn("input_name", normalized)
        self.assertEqual(normalized["input_path"], relative_path)
        self.assertEqual(normalized["input_paths"], [relative_path])


if __name__ == "__main__":
    unittest.main()
