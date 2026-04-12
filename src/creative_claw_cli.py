"""Unified command-line entrypoint for CreativeClaw."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from conf.channel import CHANNEL_CONFIG, WebChannelConfig
from src.chat_runner import run_chat_service, run_cli_chat


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CreativeClaw CLI parser."""
    parser = argparse.ArgumentParser(
        prog="creative-claw",
        description="Unified CLI entrypoint for CreativeClaw chat channels.",
    )
    command_parsers = parser.add_subparsers(dest="command")
    command_parsers.required = True

    chat_parser = command_parsers.add_parser(
        "chat",
        help="Start one CreativeClaw chat channel.",
    )
    channel_parsers = chat_parser.add_subparsers(dest="channel")
    channel_parsers.required = True

    cli_parser = channel_parsers.add_parser(
        "cli",
        help="Start the CLI terminal chat channel.",
    )
    cli_parser.add_argument(
        "--user-id",
        type=str,
        default="cli-user",
        help="Logical user ID for the CLI channel session.",
    )
    cli_parser.add_argument(
        "--chat-id",
        type=str,
        default="terminal",
        help="Logical chat ID for the CLI channel session.",
    )
    cli_parser.add_argument(
        "--message",
        type=str,
        help="Exit after sending a single message (non-interactive mode).",
    )
    cli_parser.add_argument(
        "--attachment",
        action="append",
        default=[],
        metavar="PATH",
        help="Attachment path for non-interactive mode. Repeat this flag to send multiple files.",
    )
    cli_parser.add_argument("--img1", type=str, default=None, help=argparse.SUPPRESS)
    cli_parser.add_argument("--img2", type=str, default=None, help=argparse.SUPPRESS)

    for name in ("telegram", "feishu"):
        channel_parsers.add_parser(
            name,
            help=f"Start the {name} chat channel.",
        )

    web_parser = channel_parsers.add_parser(
        "web",
        help="Start the local browser web chat channel.",
    )
    web_parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Host interface for the web chat server. Defaults to the configured WEB_HOST value.",
    )
    web_parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for the web chat server. Defaults to the configured WEB_PORT value.",
    )
    web_parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="Browser page title for the web chat surface.",
    )
    web_parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the browser automatically after the web chat server starts.",
    )

    return parser


def collect_cli_attachment_paths(args: argparse.Namespace) -> list[str]:
    """Collect CLI attachment paths including legacy compatibility flags."""
    attachment_paths = list(getattr(args, "attachment", []) or [])
    for legacy_flag in ("img1", "img2"):
        value = getattr(args, legacy_flag, None)
        if value:
            attachment_paths.append(value)
    return attachment_paths


def build_web_channel_config(args: argparse.Namespace) -> WebChannelConfig:
    """Build the effective web channel config from defaults plus CLI overrides."""
    return CHANNEL_CONFIG.web.model_copy(
        update={
            "host": args.host or CHANNEL_CONFIG.web.host,
            "port": args.port if args.port is not None else CHANNEL_CONFIG.web.port,
            "title": args.title or CHANNEL_CONFIG.web.title,
            "open_browser": bool(args.open_browser or CHANNEL_CONFIG.web.open_browser),
        }
    )


async def run_cli(args: argparse.Namespace) -> int:
    """Run the parsed CreativeClaw CLI command."""
    if args.command != "chat":
        raise ValueError(f"Unsupported command '{args.command}'.")

    if args.channel == "cli":
        await run_cli_chat(
            user_id=args.user_id,
            chat_id=args.chat_id,
            message=args.message,
            attachment_paths=collect_cli_attachment_paths(args),
        )
        return 0

    if args.channel == "web":
        await run_chat_service(args.channel, web_config=build_web_channel_config(args))
        return 0

    await run_chat_service(args.channel)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run the requested CreativeClaw CLI command."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return asyncio.run(run_cli(args))


if __name__ == "__main__":
    raise SystemExit(main())
