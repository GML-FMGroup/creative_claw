import argparse
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from conf.channel import WebChannelConfig
from src.channels.feishu import FeishuChannel
from src.channels.local import LocalChannel
from src.channels.telegram import TelegramChannel
from src.channels.web import WebChannel
from src.chat_runner import (
    build_chat_channel,
    build_cli_attachments,
    create_chat_manager,
    normalize_chat_channel_name,
)
from src.creative_claw_cli import (
    build_parser,
    build_web_channel_config,
    collect_cli_attachment_paths,
    run_cli,
)


class _FakeRuntime:
    async def run_message(self, _message):
        if False:
            yield None


class CreativeClawCliParserTests(unittest.TestCase):
    def test_build_parser_parses_init_command(self) -> None:
        args = build_parser().parse_args(["init", "--force"])

        self.assertEqual(args.command, "init")
        self.assertTrue(args.force)

    def test_build_parser_parses_cli_chat_command(self) -> None:
        args = build_parser().parse_args(
            [
                "chat",
                "cli",
                "--user-id",
                "demo-user",
                "--chat-id",
                "demo-chat",
                "--message",
                "hello",
                "--attachment",
                "one.png",
                "--attachment",
                "two.png",
            ]
        )

        self.assertEqual(args.command, "chat")
        self.assertEqual(args.channel, "cli")
        self.assertEqual(args.user_id, "demo-user")
        self.assertEqual(args.chat_id, "demo-chat")
        self.assertEqual(args.message, "hello")
        self.assertEqual(args.attachment, ["one.png", "two.png"])

    def test_collect_cli_attachment_paths_includes_legacy_flags(self) -> None:
        args = argparse.Namespace(
            attachment=["from-new-flag.png"],
            img1="from-img1.png",
            img2="from-img2.png",
        )

        self.assertEqual(
            collect_cli_attachment_paths(args),
            ["from-new-flag.png", "from-img1.png", "from-img2.png"],
        )

    def test_build_parser_parses_web_chat_command(self) -> None:
        args = build_parser().parse_args(
            ["chat", "web", "--host", "0.0.0.0", "--port", "19001", "--title", "Demo", "--open-browser"]
        )

        self.assertEqual(args.command, "chat")
        self.assertEqual(args.channel, "web")
        self.assertEqual(args.host, "0.0.0.0")
        self.assertEqual(args.port, 19001)
        self.assertEqual(args.title, "Demo")
        self.assertTrue(args.open_browser)

    def test_build_parser_rejects_removed_local_chat_command(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["chat", "local"])

    def test_build_web_channel_config_applies_cli_overrides(self) -> None:
        args = argparse.Namespace(host="0.0.0.0", port=19001, title="Demo", open_browser=True)

        with patch(
            "src.creative_claw_cli.CHANNEL_CONFIG",
            SimpleNamespace(web=WebChannelConfig(host="127.0.0.1", port=18900, title="CreativeClaw Web Chat", open_browser=False)),
        ):
            config = build_web_channel_config(args)

        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.port, 19001)
        self.assertEqual(config.title, "Demo")
        self.assertTrue(config.open_browser)


class CreativeClawCliDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_cli_dispatches_init_command(self) -> None:
        args = build_parser().parse_args(["init", "--force"])

        with patch(
            "src.creative_claw_cli.initialize_runtime_config",
            return_value=(Path("/tmp/.creative-claw/conf.json"), Path("/tmp/.creative-claw/workspace"), True),
        ) as mocked_init, patch("builtins.print") as mocked_print:
            exit_code = await run_cli(args)

        self.assertEqual(exit_code, 0)
        mocked_init.assert_called_once_with(force=True)
        self.assertEqual(mocked_print.call_count, 3)

    async def test_run_cli_dispatches_cli_chat(self) -> None:
        args = build_parser().parse_args(
            ["chat", "cli", "--message", "hello", "--attachment", "demo.png"]
        )

        with patch("src.creative_claw_cli.run_cli_chat", new=AsyncMock()) as mocked_run_cli_chat:
            exit_code = await run_cli(args)

        self.assertEqual(exit_code, 0)
        mocked_run_cli_chat.assert_awaited_once_with(
            user_id="cli-user",
            chat_id="terminal",
            message="hello",
            attachment_paths=["demo.png"],
        )

    async def test_run_cli_dispatches_remote_channel_service(self) -> None:
        args = build_parser().parse_args(["chat", "telegram"])

        with patch("src.creative_claw_cli.run_chat_service", new=AsyncMock()) as mocked_run_chat_service:
            exit_code = await run_cli(args)

        self.assertEqual(exit_code, 0)
        mocked_run_chat_service.assert_awaited_once_with("telegram")

    async def test_run_cli_dispatches_web_channel_service_with_config(self) -> None:
        args = build_parser().parse_args(["chat", "web", "--port", "19001", "--title", "Demo"])
        web_config = WebChannelConfig(host="127.0.0.1", port=19001, title="Demo", open_browser=False)

        with patch("src.creative_claw_cli.build_web_channel_config", return_value=web_config), patch(
            "src.creative_claw_cli.run_chat_service",
            new=AsyncMock(),
        ) as mocked_run_chat_service:
            exit_code = await run_cli(args)

        self.assertEqual(exit_code, 0)
        mocked_run_chat_service.assert_awaited_once_with("web", web_config=web_config)


class ChatRunnerTests(unittest.TestCase):
    def test_normalize_chat_channel_name_rejects_unknown_channel(self) -> None:
        with self.assertRaises(ValueError):
            normalize_chat_channel_name("unknown")

    def test_build_chat_channel_returns_cli_channel(self) -> None:
        channel = build_chat_channel("cli", inbound_handler=AsyncMock(), cli_writer=lambda _line: None)

        self.assertIsInstance(channel, LocalChannel)

    def test_build_chat_channel_uses_telegram_config(self) -> None:
        config = SimpleNamespace(
            telegram=SimpleNamespace(bot_token="telegram-token", allow_from=["1001"]),
            feishu=SimpleNamespace(
                app_id="",
                app_secret="",
                encrypt_key="",
                verification_token="",
                allow_from=[],
            ),
        )

        with patch("src.chat_runner.CHANNEL_CONFIG", config):
            channel = build_chat_channel("telegram", inbound_handler=AsyncMock())

        self.assertIsInstance(channel, TelegramChannel)
        self.assertEqual(channel.token, "telegram-token")
        self.assertEqual(channel.allow_from, {"1001"})

    def test_build_chat_channel_uses_feishu_config(self) -> None:
        config = SimpleNamespace(
            telegram=SimpleNamespace(bot_token="", allow_from=[]),
            feishu=SimpleNamespace(
                app_id="app-id",
                app_secret="app-secret",
                encrypt_key="encrypt-key",
                verification_token="verification-token",
                allow_from=["ou_demo"],
            ),
        )

        with patch("src.chat_runner.CHANNEL_CONFIG", config):
            channel = build_chat_channel("feishu", inbound_handler=AsyncMock())

        self.assertIsInstance(channel, FeishuChannel)
        self.assertEqual(channel.app_id, "app-id")
        self.assertEqual(channel.app_secret, "app-secret")
        self.assertEqual(channel.allow_from, {"ou_demo"})

    def test_build_chat_channel_uses_web_config(self) -> None:
        config = SimpleNamespace(
            telegram=SimpleNamespace(bot_token="", allow_from=[]),
            feishu=SimpleNamespace(
                app_id="",
                app_secret="",
                encrypt_key="",
                verification_token="",
                allow_from=[],
            ),
            web=WebChannelConfig(host="127.0.0.1", port=18900, title="CreativeClaw Web Chat", open_browser=False),
        )

        with patch("src.chat_runner.CHANNEL_CONFIG", config):
            channel = build_chat_channel("web", inbound_handler=AsyncMock())

        self.assertIsInstance(channel, WebChannel)
        self.assertEqual(channel.config.title, "CreativeClaw Web Chat")

    def test_create_chat_manager_registers_requested_channel(self) -> None:
        manager, channel = create_chat_manager("cli", runtime=_FakeRuntime())

        self.assertIs(manager.channels["cli"], channel)

    def test_build_cli_attachments_keeps_existing_files_only(self) -> None:
        warnings: list[str] = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            existing_path = Path(tmp_dir) / "demo.png"
            existing_path.write_bytes(b"demo")

            attachments = build_cli_attachments(
                [str(existing_path), str(Path(tmp_dir) / "missing.png")],
                warn=warnings.append,
            )

        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].path, str(existing_path))
        self.assertEqual(warnings, [f"warning: attachment not found: {Path(tmp_dir) / 'missing.png'}"])


if __name__ == "__main__":
    unittest.main()
