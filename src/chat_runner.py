"""Shared chat channel bootstrap helpers for the CreativeClaw CLI."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING

from conf.channel import CHANNEL_CONFIG, WebChannelConfig

if TYPE_CHECKING:
    from src.channels.base import BaseChannel
    from src.channels.manager import ChannelManager
    from src.runtime import CreativeClawRuntime, InboundMessage, MessageAttachment

SUPPORTED_CHAT_CHANNELS: tuple[str, ...] = ("cli", "telegram", "feishu", "web")

_CHANNEL_LABELS = {
    "cli": "CLI",
    "telegram": "Telegram",
    "feishu": "Feishu",
    "web": "Web",
}


def normalize_chat_channel_name(channel_name: str) -> str:
    """Return one validated chat channel name."""
    normalized = str(channel_name or "").strip().lower()
    if normalized not in SUPPORTED_CHAT_CHANNELS:
        supported = ", ".join(SUPPORTED_CHAT_CHANNELS)
        raise ValueError(f"Unsupported chat channel '{channel_name}'. Supported channels: {supported}.")
    return normalized


def build_chat_channel(
    channel_name: str,
    *,
    inbound_handler: Callable[[InboundMessage], Awaitable[None]],
    cli_writer: Callable[[str], None] | None = None,
    web_config: WebChannelConfig | None = None,
) -> BaseChannel:
    """Build one configured chat channel implementation."""
    normalized = normalize_chat_channel_name(channel_name)

    if normalized == "cli":
        from src.channels.local import LocalChannel

        return LocalChannel(writer=cli_writer)

    if normalized == "telegram":
        from src.channels.telegram import TelegramChannel

        return TelegramChannel(
            token=CHANNEL_CONFIG.telegram.bot_token,
            allow_from=CHANNEL_CONFIG.telegram.allow_from,
            inbound_handler=inbound_handler,
        )

    if normalized == "web":
        from src.channels.web import WebChannel

        return WebChannel(
            config=web_config or CHANNEL_CONFIG.web,
            inbound_handler=inbound_handler,
        )

    from src.channels.feishu import FeishuChannel

    return FeishuChannel(
        app_id=CHANNEL_CONFIG.feishu.app_id,
        app_secret=CHANNEL_CONFIG.feishu.app_secret,
        encrypt_key=CHANNEL_CONFIG.feishu.encrypt_key,
        verification_token=CHANNEL_CONFIG.feishu.verification_token,
        allow_from=CHANNEL_CONFIG.feishu.allow_from,
        inbound_handler=inbound_handler,
    )


def create_chat_manager(
    channel_name: str,
    *,
    runtime: CreativeClawRuntime | None = None,
    cli_writer: Callable[[str], None] | None = None,
    web_config: WebChannelConfig | None = None,
) -> tuple[ChannelManager, BaseChannel]:
    """Create one runtime-backed manager together with the requested channel."""
    from src.channels.manager import ChannelManager
    from src.runtime import CreativeClawRuntime

    active_runtime = runtime or CreativeClawRuntime()
    manager = ChannelManager(active_runtime)
    channel = build_chat_channel(
        channel_name,
        inbound_handler=manager.handle_inbound,
        cli_writer=cli_writer,
        web_config=web_config,
    )
    manager.register(channel)
    return manager, channel


def build_cli_attachments(
    paths: Sequence[str],
    *,
    warn: Callable[[str], None] | None = None,
) -> list[MessageAttachment]:
    """Convert CLI attachment paths into normalized runtime attachments."""
    from src.runtime import MessageAttachment

    warnings = warn or print
    attachments: list[MessageAttachment] = []
    for raw_path in paths:
        cleaned_path = str(raw_path or "").strip()
        if not cleaned_path:
            continue
        if not os.path.exists(cleaned_path):
            warnings(f"warning: attachment not found: {cleaned_path}")
            continue
        attachments.append(
            MessageAttachment(
                path=cleaned_path,
                name=os.path.basename(cleaned_path),
            )
        )
    return attachments


async def send_cli_chat_message(
    manager: ChannelManager,
    *,
    prompt: str,
    user_id: str,
    chat_id: str,
    attachment_paths: Sequence[str],
    status_writer: Callable[[str], None] | None = None,
    warn: Callable[[str], None] | None = None,
) -> None:
    """Send one normalized CLI chat message through the shared manager."""
    from src.runtime import InboundMessage

    writer = status_writer or print
    writer(f"\nCLI: sending instruction '{prompt}' (chat: {chat_id}, user: {user_id})")
    await manager.handle_inbound(
        InboundMessage(
            channel="cli",
            sender_id=user_id,
            chat_id=chat_id,
            text=prompt,
            attachments=build_cli_attachments(attachment_paths, warn=warn),
        )
    )


async def run_cli_chat(
    *,
    user_id: str,
    chat_id: str,
    message: str | None = None,
    attachment_paths: Sequence[str] = (),
    runtime: CreativeClawRuntime | None = None,
    status_writer: Callable[[str], None] | None = None,
    cli_writer: Callable[[str], None] | None = None,
    input_reader: Callable[[str], str] | None = None,
) -> None:
    """Run the CLI terminal chat flow."""
    writer = status_writer or print
    prompt_reader = input_reader or input
    manager, _ = create_chat_manager(
        "cli",
        runtime=runtime,
        cli_writer=cli_writer or writer,
    )
    await manager.start_all()
    try:
        writer(f"\nChatting with CreativeClaw (user: {user_id}, chat: {chat_id}).")

        if message:
            await send_cli_chat_message(
                manager,
                prompt=message,
                user_id=user_id,
                chat_id=chat_id,
                attachment_paths=attachment_paths,
                status_writer=writer,
                warn=writer,
            )
            return

        writer("Type 'exit' to quit.")
        while True:
            try:
                user_message = prompt_reader("\nYou (instruction): ").strip()
                if user_message.lower() == "exit":
                    writer("exiting ...")
                    break
                if not user_message:
                    continue

                attachment_text = prompt_reader("Attachment path(s) (optional, comma separated): ").strip()
                raw_paths = [item.strip() for item in attachment_text.split(",")] if attachment_text else []
                await send_cli_chat_message(
                    manager,
                    prompt=user_message,
                    user_id=user_id,
                    chat_id=chat_id,
                    attachment_paths=raw_paths,
                    status_writer=writer,
                    warn=writer,
                )
            except KeyboardInterrupt:
                writer("\nexiting ...")
                break
            except Exception as exc:  # pragma: no cover - exercised interactively
                writer(f"error: {exc}")
                import traceback

                traceback.print_exc()
    finally:
        await manager.stop_all()


async def run_chat_service(
    channel_name: str,
    *,
    runtime: CreativeClawRuntime | None = None,
    status_writer: Callable[[str], None] | None = None,
    web_config: WebChannelConfig | None = None,
) -> None:
    """Start one long-running non-CLI chat channel."""
    normalized = normalize_chat_channel_name(channel_name)
    if normalized == "cli":
        raise ValueError("CLI chat should be run through run_cli_chat().")

    writer = status_writer or print
    manager, channel = create_chat_manager(normalized, runtime=runtime, web_config=web_config)
    label = _CHANNEL_LABELS[normalized]
    await manager.start_all()
    if normalized == "web" and hasattr(channel, "url"):
        writer(f"{label} channel is running at {channel.url}. Press Ctrl+C to stop.")
    else:
        writer(f"{label} channel is running. Press Ctrl+C to stop.")
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        writer(f"\nStopping {label} channel ...")
    finally:
        await manager.stop_all()
