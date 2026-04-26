import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
        ctx = _build_ctx(
            {
                "current_parameters": {"prompt": "a toy corgi"},
                "turn_index": 1,
                "step": 1,
                "expert_step": 1,
            }
        )
        fake_output_path = (
            workspace_root()
            / "generated"
            / "session_1"
            / "turn_1"
            / "turn1_step1_3d_generation_job_1"
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
            turn_index=1,
            step=1,
        )
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["job_id"], "job-1")
        self.assertEqual(
            current_output["output_files"][0]["path"],
            "generated/session_1/turn_1/turn1_step1_3d_generation_job_1/hy3d_result_1_mesh.fbx",
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

    async def test_3d_generation_routes_seed3d_provider(self) -> None:
        agent = ThreeDGenerationAgent(name="ThreeDGenerationAgent", public_name="3DGeneration")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "provider": "seed3d",
                    "input_path": "inbox/cli/session_1/object.png",
                    "file_format": "usdz",
                    "subdivision_level": "high",
                },
                "turn_index": 1,
                "step": 1,
                "expert_step": 1,
            }
        )
        fake_output_path = (
            workspace_root()
            / "generated"
            / "session_1"
            / "turn_1"
            / "turn1_step1_3d_generation_task_1"
            / "seed3d_result_1.usdz"
        )

        with patch(
            "src.agents.experts.three_d_generation.three_d_generation_agent.generation_tools.seed3d_generate_tool",
            new=AsyncMock(
                return_value={
                    "status": "success",
                    "message": "Seed3D task task-1 succeeded with 1 file(s).",
                    "provider": "seed3d",
                    "model_name": "doubao-seed3d-2-0-260328",
                    "job_id": "task-1",
                    "generate_type": "image_to_3d",
                    "file_format": "usdz",
                    "subdivision_level": "high",
                    "downloaded_files": [
                        {
                            "path": fake_output_path,
                            "type": "usdz",
                            "url": "https://example.com/seed3d.usdz",
                            "preview_image_url": "",
                        }
                    ],
                }
            ),
        ) as seed3d_mock:
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        seed3d_mock.assert_awaited_once_with(
            input_path="inbox/cli/session_1/object.png",
            image_url=None,
            model="doubao-seed3d-2-0-260328",
            file_format="usdz",
            subdivision_level="high",
            timeout_seconds=900,
            interval_seconds=60,
            session_id="session_1",
            turn_index=1,
            step=1,
        )
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["provider"], "seed3d")
        self.assertEqual(current_output["file_format"], "usdz")
        self.assertEqual(
            current_output["output_files"][0]["path"],
            "generated/session_1/turn_1/turn1_step1_3d_generation_task_1/seed3d_result_1.usdz",
        )

    async def test_seed3d_provider_requires_one_image_source(self) -> None:
        agent = ThreeDGenerationAgent(name="ThreeDGenerationAgent", public_name="3DGeneration")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "provider": "seed3d",
                    "prompt": "make a 3D toy",
                },
                "step": 0,
            }
        )

        with patch(
            "src.agents.experts.three_d_generation.three_d_generation_agent.generation_tools.seed3d_generate_tool",
            new=AsyncMock(),
        ) as seed3d_mock:
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        seed3d_mock.assert_not_called()
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("provider `seed3d` requires", current_output["message"])

    async def test_3d_generation_routes_hyper3d_provider(self) -> None:
        agent = ThreeDGenerationAgent(name="ThreeDGenerationAgent", public_name="3DGeneration")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "provider": "hyper3d",
                    "prompt": "full-body sci-fi robot",
                    "image_urls": ["https://example.com/front.png"],
                    "file_format": "fbx",
                    "mesh_mode": "Raw",
                    "material": "PBR",
                    "quality_override": 150000,
                    "hd_texture": True,
                },
                "turn_index": 1,
                "step": 1,
                "expert_step": 1,
            }
        )
        fake_output_path = (
            workspace_root()
            / "generated"
            / "session_1"
            / "turn_1"
            / "turn1_step1_3d_generation_task_2"
            / "hyper3d_result_1.zip"
        )

        with patch(
            "src.agents.experts.three_d_generation.three_d_generation_agent.generation_tools.hyper3d_generate_tool",
            new=AsyncMock(
                return_value={
                    "status": "success",
                    "message": "Hyper3D task task-2 succeeded with 1 file(s).",
                    "provider": "hyper3d",
                    "model_name": "hyper3d-gen2-260112",
                    "job_id": "task-2",
                    "generate_type": "image_to_3d",
                    "file_format": "fbx",
                    "subdivision_level": "",
                    "downloaded_files": [
                        {
                            "path": fake_output_path,
                            "type": "fbx",
                            "url": "https://example.com/hyper3d.zip",
                            "preview_image_url": "",
                        }
                    ],
                }
            ),
        ) as hyper3d_mock:
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        hyper3d_mock.assert_awaited_once_with(
            prompt="full-body sci-fi robot",
            input_paths=[],
            image_urls=["https://example.com/front.png"],
            model="hyper3d-gen2-260112",
            file_format="fbx",
            subdivision_level=None,
            material="PBR",
            mesh_mode="Raw",
            quality_override=150000,
            addons=None,
            use_original_alpha=None,
            bbox_condition=None,
            ta_pose=None,
            hd_texture=True,
            timeout_seconds=900,
            interval_seconds=60,
            session_id="session_1",
            turn_index=1,
            step=1,
        )
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["provider"], "hyper3d")
        self.assertEqual(current_output["file_format"], "fbx")

    async def test_3d_generation_routes_hitem3d_provider(self) -> None:
        agent = ThreeDGenerationAgent(name="ThreeDGenerationAgent", public_name="3DGeneration")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "provider": "hitem3d",
                    "image_urls": ["https://example.com/front.png", "https://example.com/left.png"],
                    "file_format": "glb",
                    "resolution": "1536pro",
                    "face_count": 2000000,
                    "request_type": 3,
                    "multi_images_bit": "1010",
                },
                "turn_index": 1,
                "step": 1,
                "expert_step": 1,
            }
        )
        fake_output_path = (
            workspace_root()
            / "generated"
            / "session_1"
            / "turn_1"
            / "turn1_step1_3d_generation_task_3"
            / "hitem3d_result_1.zip"
        )

        with patch(
            "src.agents.experts.three_d_generation.three_d_generation_agent.generation_tools.hitem3d_generate_tool",
            new=AsyncMock(
                return_value={
                    "status": "success",
                    "message": "Hitem3D task task-3 succeeded with 1 file(s).",
                    "provider": "hitem3d",
                    "model_name": "hitem3d-2-0-251223",
                    "job_id": "task-3",
                    "generate_type": "image_to_3d",
                    "file_format": "glb",
                    "resolution": "1536pro",
                    "downloaded_files": [
                        {
                            "path": fake_output_path,
                            "type": "glb",
                            "url": "https://example.com/hitem3d.zip",
                            "preview_image_url": "",
                        }
                    ],
                }
            ),
        ) as hitem3d_mock:
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        hitem3d_mock.assert_awaited_once_with(
            image_urls=["https://example.com/front.png", "https://example.com/left.png"],
            model="hitem3d-2-0-251223",
            file_format="glb",
            resolution="1536pro",
            face_count=2000000,
            request_type=3,
            multi_images_bit="1010",
            timeout_seconds=900,
            interval_seconds=60,
            session_id="session_1",
            turn_index=1,
            step=1,
        )
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["provider"], "hitem3d")
        self.assertEqual(current_output["resolution"], "1536pro")

    async def test_hitem3d_provider_requires_remote_image_url(self) -> None:
        agent = ThreeDGenerationAgent(name="ThreeDGenerationAgent", public_name="3DGeneration")
        ctx = _build_ctx(
            {
                "current_parameters": {
                    "provider": "hitem3d",
                    "input_path": "inbox/cli/session_1/object.png",
                },
                "step": 0,
            }
        )

        with patch(
            "src.agents.experts.three_d_generation.three_d_generation_agent.generation_tools.hitem3d_generate_tool",
            new=AsyncMock(),
        ) as hitem3d_mock:
            events = [event async for event in agent._run_async_impl(ctx)]

        self.assertEqual(len(events), 1)
        hitem3d_mock.assert_not_called()
        current_output = events[0].actions.state_delta["current_output"]
        self.assertEqual(current_output["status"], "error")
        self.assertIn("externally accessible", current_output["message"])


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
            / "turn_1"
            / "turn1_step1_3d_generation_job_1"
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
                turn_index=1,
                step=1,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["job_id"], "job-1")
        self.assertEqual(result["downloaded_files"][0]["path"], fake_output_path)

    async def test_seed3d_generate_tool_builds_ark_task_and_downloads_result(self) -> None:
        create_mock = MagicMock(return_value=SimpleNamespace(id="task-1"))
        fake_client = SimpleNamespace(
            content_generation=SimpleNamespace(
                tasks=SimpleNamespace(create=create_mock)
            )
        )
        fake_query_response = SimpleNamespace(
            status="succeeded",
            content=SimpleNamespace(
                model_url="https://example.com/seed3d_result.usdz",
                preview_image_url="https://example.com/preview.png",
            ),
        )
        fake_output_path = (
            workspace_root()
            / "generated"
            / "session_1"
            / "turn_1"
            / "turn1_step1_3d_generation_task_1"
            / "seed3d_result_1.usdz"
        )

        with (
            patch(
                "src.agents.experts.three_d_generation.tool._build_ark_client_from_env",
                return_value=fake_client,
            ),
            patch(
                "src.agents.experts.three_d_generation.tool._image_file_to_data_url",
                return_value="data:image/png;base64,abc",
            ),
            patch(
                "src.agents.experts.three_d_generation.tool._poll_seed3d_task_until_finished",
                new=AsyncMock(return_value=fake_query_response),
            ),
            patch(
                "src.agents.experts.three_d_generation.tool._build_download_dir",
                return_value=fake_output_path.parent,
            ),
            patch(
                "src.agents.experts.three_d_generation.tool._download_seed3d_result_files_sync",
                return_value=[
                    {
                        "path": fake_output_path,
                        "type": "usdz",
                        "url": "https://example.com/seed3d_result.usdz",
                        "preview_image_url": "",
                    }
                ],
            ) as download_mock,
        ):
            result = await generation_tools.seed3d_generate_tool(
                input_path="inbox/cli/session_1/object.png",
                model="doubao-seed3d-2-0-260328",
                file_format="usdz",
                subdivision_level="high",
                session_id="session_1",
                turn_index=1,
                step=1,
            )

        self.assertEqual(result["status"], "success")
        create_mock.assert_called_once_with(
            model="doubao-seed3d-2-0-260328",
            content=[
                {"type": "text", "text": "--subdivisionlevel high --fileformat usdz"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        )
        download_mock.assert_called_once_with(
            [
                {
                    "url": "https://example.com/seed3d_result.usdz",
                    "type": "usdz",
                    "preview_image_url": "",
                }
            ],
            fake_output_path.parent,
            file_format="usdz",
        )
        self.assertEqual(result["job_id"], "task-1")
        self.assertEqual(result["downloaded_files"][0]["path"], fake_output_path)

    async def test_hyper3d_generate_tool_builds_ark_task_and_downloads_result(self) -> None:
        create_mock = MagicMock(return_value=SimpleNamespace(id="task-2"))
        fake_client = SimpleNamespace(
            content_generation=SimpleNamespace(
                tasks=SimpleNamespace(create=create_mock)
            )
        )
        fake_query_response = SimpleNamespace(
            status="succeeded",
            content=SimpleNamespace(file_url="https://example.com/hyper3d_result.zip"),
        )
        fake_output_path = (
            workspace_root()
            / "generated"
            / "session_1"
            / "turn_1"
            / "turn1_step1_3d_generation_task_2"
            / "hyper3d_result_1.zip"
        )

        with (
            patch(
                "src.agents.experts.three_d_generation.tool._build_ark_client_from_env",
                return_value=fake_client,
            ),
            patch(
                "src.agents.experts.three_d_generation.tool._poll_ark_3d_task_until_finished",
                new=AsyncMock(return_value=fake_query_response),
            ),
            patch(
                "src.agents.experts.three_d_generation.tool._build_download_dir",
                return_value=fake_output_path.parent,
            ),
            patch(
                "src.agents.experts.three_d_generation.tool._download_seed3d_result_files_sync",
                return_value=[
                    {
                        "path": fake_output_path,
                        "type": "fbx",
                        "url": "https://example.com/hyper3d_result.zip",
                        "preview_image_url": "",
                    }
                ],
            ) as download_mock,
        ):
            result = await generation_tools.hyper3d_generate_tool(
                prompt="full-body sci-fi robot",
                image_urls=["https://example.com/front.png"],
                model="hyper3d-gen2-260112",
                file_format="fbx",
                mesh_mode="Raw",
                material="PBR",
                quality_override=150000,
                hd_texture=True,
                session_id="session_1",
                turn_index=1,
                step=1,
            )

        self.assertEqual(result["status"], "success")
        create_mock.assert_called_once_with(
            model="hyper3d-gen2-260112",
            content=[
                {
                    "type": "text",
                    "text": (
                        "full-body sci-fi robot --mesh_mode Raw --hd_texture true "
                        "--material PBR --quality_override 150000 --fileformat fbx"
                    ),
                },
                {"type": "image_url", "image_url": {"url": "https://example.com/front.png"}},
            ],
        )
        download_mock.assert_called_once_with(
            [
                {
                    "url": "https://example.com/hyper3d_result.zip",
                    "type": "fbx",
                    "preview_image_url": "",
                }
            ],
            fake_output_path.parent,
            file_format="fbx",
            provider_prefix="hyper3d",
        )
        self.assertEqual(result["job_id"], "task-2")

    async def test_hitem3d_generate_tool_builds_ark_task_and_downloads_result(self) -> None:
        create_mock = MagicMock(return_value=SimpleNamespace(id="task-3"))
        fake_client = SimpleNamespace(
            content_generation=SimpleNamespace(
                tasks=SimpleNamespace(create=create_mock)
            )
        )
        fake_query_response = SimpleNamespace(
            status="succeeded",
            content=SimpleNamespace(file_url="https://example.com/hitem3d_result.zip"),
        )
        fake_output_path = (
            workspace_root()
            / "generated"
            / "session_1"
            / "turn_1"
            / "turn1_step1_3d_generation_task_3"
            / "hitem3d_result_1.zip"
        )

        with (
            patch(
                "src.agents.experts.three_d_generation.tool._build_ark_client_from_env",
                return_value=fake_client,
            ),
            patch(
                "src.agents.experts.three_d_generation.tool._poll_ark_3d_task_until_finished",
                new=AsyncMock(return_value=fake_query_response),
            ),
            patch(
                "src.agents.experts.three_d_generation.tool._build_download_dir",
                return_value=fake_output_path.parent,
            ),
            patch(
                "src.agents.experts.three_d_generation.tool._download_seed3d_result_files_sync",
                return_value=[
                    {
                        "path": fake_output_path,
                        "type": "glb",
                        "url": "https://example.com/hitem3d_result.zip",
                        "preview_image_url": "",
                    }
                ],
            ) as download_mock,
        ):
            result = await generation_tools.hitem3d_generate_tool(
                image_urls=["https://example.com/front.png", "https://example.com/left.png"],
                model="hitem3d-2-0-251223",
                file_format="glb",
                resolution="1536pro",
                face_count=2000000,
                request_type=3,
                multi_images_bit="1010",
                session_id="session_1",
                turn_index=1,
                step=1,
            )

        self.assertEqual(result["status"], "success")
        create_mock.assert_called_once_with(
            model="hitem3d-2-0-251223",
            content=[
                {
                    "type": "text",
                    "text": "--resolution 1536pro --request_type 3 --ff 2 --face 2000000 --multi_images_bit 1010",
                },
                {"type": "image_url", "image_url": {"url": "https://example.com/front.png"}},
                {"type": "image_url", "image_url": {"url": "https://example.com/left.png"}},
            ],
        )
        download_mock.assert_called_once_with(
            [
                {
                    "url": "https://example.com/hitem3d_result.zip",
                    "type": "glb",
                    "preview_image_url": "",
                }
            ],
            fake_output_path.parent,
            file_format="glb",
            provider_prefix="hitem3d",
        )
        self.assertEqual(result["job_id"], "task-3")
