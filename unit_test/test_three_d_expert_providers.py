import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from conf.schema import CreativeClawConfig
from conf.app_config import save_app_config, get_config_path
from src.agents.experts.three_d_generation import tool as generation_tools
from src.agents.experts.three_d_generation.three_d_generation_agent import (
    ThreeDGenerationAgent,
)
from src.runtime.workspace import workspace_root


def _build_ctx(state: dict) -> SimpleNamespace:
    return SimpleNamespace(
        session=SimpleNamespace(
            state=state,
            app_name="test_app",
            user_id="user_1",
            id="session_1",
        ),
    )


class ThreeDGenerationAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_3d_generation_uses_hy3d_by_default(self) -> None:
        agent = ThreeDGenerationAgent(name="ThreeDGenerationAgent", public_name="3DGeneration")
        ctx = _build_ctx({"current_parameters": {"prompt": "a toy corgi"}, "step": 0})
        fake_output_path = (
            workspace_root()
            / "generated"
            / "session_1"
            / "step1_3d_generation_job_1"
            / "hy3d_result_1_mesh.fbx"
        )

        with patch(
            "src.agents.experts.three_d_generation.three_d_generation_agent.generation_tools.hy3d_generate_tool",
            new=AsyncMock(
                return_value={
                    "status": "success",
                    "message": "hy3d job job-1 succeeded with 1 file(s).",
                    "provider": "hy3d",
                    "model_name": "3.0",
                    "job_id": "job-1",
                    "generate_type": "Normal",
                    "downloaded_files": [
                        {
                            "path": fake_output_path,
                            "type": "mesh",
                            "url": "https://example.com/hy3d.fbx",
                            "preview_image_url": "https://example.com/preview.png",
                        }
                    ],
                }
            ),
        ) as hy3d_mock:
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        hy3d_mock.assert_awaited_once_with(
            prompt="a toy corgi",
            input_path=None,
            model="3.0",
            enable_pbr=False,
            generate_type="Normal",
            face_count=None,
            polygon_type=None,
            result_format=None,
            timeout_seconds=900,
            interval_seconds=8,
            session_id="session_1",
            step=1,
        )
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["job_id"], "job-1")
        self.assertEqual(
            current_output["output_files"][0]["path"],
            "generated/session_1/step1_3d_generation_job_1/hy3d_result_1_mesh.fbx",
        )

    async def test_3d_generation_requires_sketch_for_prompt_plus_image(self) -> None:
        agent = ThreeDGenerationAgent(name="ThreeDGenerationAgent", public_name="3DGeneration")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "prompt": "wood carving style",
                    "input_path": "inbox/cli/session_1/sketch.png",
                    "generate_type": "normal",
                },
                "step": 0,
            }
        )

        with patch(
            "src.agents.experts.three_d_generation.three_d_generation_agent.generation_tools.hy3d_generate_tool",
            new=AsyncMock(),
        ) as hy3d_mock:
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        hy3d_mock.assert_not_called()
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("generate_type=sketch", current_output["message"])


class ThreeDGenerationToolTests(unittest.IsolatedAsyncioTestCase):
    def test_build_client_from_env_reads_tencent_credentials_from_conf_json(self) -> None:
        fake_models = object()
        fake_sdk_exception = RuntimeError
        fake_credential = object()
        fake_credential_cls = unittest.mock.Mock(return_value=fake_credential)
        fake_ai3d_client_module = SimpleNamespace(
            Ai3dClient=unittest.mock.Mock(return_value="client-instance")
        )

        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"CREATIVE_CLAW_HOME": tmp_dir},
            clear=False,
        ):
            config = CreativeClawConfig(workspace=str(get_config_path().parent / "workspace"))
            config.services.tencentcloud_secret_id = "conf-secret-id"
            config.services.tencentcloud_secret_key = "conf-secret-key"
            config.services.tencentcloud_session_token = "conf-session-token"
            config.services.tencentcloud_region = "ap-shanghai"
            save_app_config(config)

            with patch(
                "src.agents.experts.three_d_generation.tool._load_tencentcloud_sdk",
                return_value=(
                    fake_ai3d_client_module,
                    fake_models,
                    fake_credential_cls,
                    fake_sdk_exception,
                ),
            ):
                client, models, sdk_exception = generation_tools._build_client_from_env()

        fake_credential_cls.assert_called_once_with(
            "conf-secret-id",
            "conf-secret-key",
            "conf-session-token",
        )
        fake_ai3d_client_module.Ai3dClient.assert_called_once_with(fake_credential, "ap-shanghai")
        self.assertEqual(client, "client-instance")
        self.assertIs(models, fake_models)
        self.assertIs(sdk_exception, fake_sdk_exception)

    def test_build_client_from_env_falls_back_to_environment_variables(self) -> None:
        fake_models = object()
        fake_sdk_exception = RuntimeError
        fake_credential = object()
        fake_credential_cls = unittest.mock.Mock(return_value=fake_credential)
        fake_ai3d_client_module = SimpleNamespace(
            Ai3dClient=unittest.mock.Mock(return_value="client-instance")
        )

        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {
                "CREATIVE_CLAW_HOME": tmp_dir,
                "TENCENTCLOUD_SECRET_ID": "env-secret-id",
                "TENCENTCLOUD_SECRET_KEY": "env-secret-key",
                "TENCENTCLOUD_SESSION_TOKEN": "env-session-token",
                "TENCENTCLOUD_REGION": "ap-beijing",
            },
            clear=False,
        ):
            save_app_config(CreativeClawConfig(workspace=str(get_config_path().parent / "workspace")))

            with patch(
                "src.agents.experts.three_d_generation.tool._load_tencentcloud_sdk",
                return_value=(
                    fake_ai3d_client_module,
                    fake_models,
                    fake_credential_cls,
                    fake_sdk_exception,
                ),
            ):
                client, models, sdk_exception = generation_tools._build_client_from_env()

        fake_credential_cls.assert_called_once_with(
            "env-secret-id",
            "env-secret-key",
            "env-session-token",
        )
        fake_ai3d_client_module.Ai3dClient.assert_called_once_with(fake_credential, "ap-beijing")
        self.assertEqual(client, "client-instance")
        self.assertIs(models, fake_models)
        self.assertIs(sdk_exception, fake_sdk_exception)

    async def test_hy3d_generate_tool_returns_downloaded_files(self) -> None:
        fake_output_path = (
            workspace_root()
            / "generated"
            / "session_1"
            / "step1_3d_generation_job_1"
            / "hy3d_result_1_mesh.fbx"
        )
        fake_query_response = SimpleNamespace(
            Status="DONE",
            ResultFile3Ds=[
                SimpleNamespace(
                    Url="https://example.com/hy3d.fbx",
                    Type="mesh",
                    PreviewImageUrl="https://example.com/preview.png",
                )
            ],
        )

        with (
            patch(
                "src.agents.experts.three_d_generation.tool._build_client_from_env",
                return_value=(SimpleNamespace(), SimpleNamespace(), RuntimeError),
            ),
            patch(
                "src.agents.experts.three_d_generation.tool._build_submit_request",
                return_value=SimpleNamespace(Model="3.0"),
            ),
            patch(
                "src.agents.experts.three_d_generation.tool._submit_job_sync",
                return_value="job-1",
            ),
            patch(
                "src.agents.experts.three_d_generation.tool._poll_job_until_finished",
                new=AsyncMock(return_value=fake_query_response),
            ),
            patch(
                "src.agents.experts.three_d_generation.tool._build_download_dir",
                return_value=fake_output_path.parent,
            ),
            patch(
                "src.agents.experts.three_d_generation.tool._download_result_files_sync",
                return_value=[
                    {
                        "path": fake_output_path,
                        "type": "mesh",
                        "url": "https://example.com/hy3d.fbx",
                        "preview_image_url": "https://example.com/preview.png",
                    }
                ],
            ),
        ):
            result = await generation_tools.hy3d_generate_tool(
                prompt="a toy corgi",
                input_path=None,
                session_id="session_1",
                step=1,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["job_id"], "job-1")
        self.assertEqual(result["downloaded_files"][0]["path"], fake_output_path)
