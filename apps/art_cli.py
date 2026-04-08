import asyncio
import argparse
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.channels import ChannelManager, LocalChannel
from src.runtime import CreativeClawRuntime, InboundMessage, MessageAttachment


def _build_attachments(paths: list[str]) -> list[MessageAttachment]:
    """Convert CLI image paths into normalized attachments."""
    attachments: list[MessageAttachment] = []
    for raw_path in paths:
        cleaned_path = raw_path.strip()
        if not cleaned_path:
            continue
        if not os.path.exists(cleaned_path):
            print(f"warning: attachment not found: {cleaned_path}")
            continue
        attachments.append(
            MessageAttachment(
                path=cleaned_path,
                name=os.path.basename(cleaned_path),
            )
        )
    return attachments


async def send_chat_message(
    manager: ChannelManager,
    core_prompt: str,
    user_id: str,
    chat_id: str,
    attachment_paths: list[str],
) -> None:
    """Send one normalized inbound message through the local channel."""
    print(f"\nCLI: sending instruction '{core_prompt}' (chat: {chat_id}, user: {user_id})")
    await manager.handle_inbound(
        InboundMessage(
            channel="local",
            sender_id=user_id,
            chat_id=chat_id,
            text=core_prompt,
            attachments=_build_attachments(attachment_paths),
        )
    )


async def main():
    parser = argparse.ArgumentParser(description="CreativeClaw CLI.")
    parser.add_argument("--user-id", type=str, default="local-user", help="Logical user ID for the channel session.")
    parser.add_argument("--chat-id", type=str, default="terminal", help="Logical chat ID for the channel session.")
    parser.add_argument("--message", type=str, help="Exit after sending a single message (non interactive mode).")
    parser.add_argument("--img1", type=str, default=None, help="Image 1 path in non interactive mode.")
    parser.add_argument("--img2", type=str, default=None, help="Image 2 path in non interactive mode.")

    args = parser.parse_args()

    runtime = CreativeClawRuntime()
    manager = ChannelManager(runtime)
    manager.register(LocalChannel())
    await manager.start_all()
    try:
        print(f"\nChatting with CreativeClaw (user: {args.user_id}, chat: {args.chat_id}).")

        if args.message:
            await send_chat_message(
                manager,
                args.message,
                args.user_id,
                args.chat_id,
                [args.img1 or "", args.img2 or ""],
            )
            return

        print("Type 'exit' to quit.")
        while True:
            try:
                user_message_base = input("\nYou (instruction): ").strip()
                if user_message_base.lower() == "exit":
                    print("exiting ...")
                    break
                if not user_message_base:
                    continue

                img1_path_text = input("Attachment path 1 (optional, press Enter to skip): ").strip()
                img2_path_text = input("Attachment path 2 (optional, press Enter to skip): ").strip()
                await send_chat_message(
                    manager,
                    user_message_base,
                    args.user_id,
                    args.chat_id,
                    [img1_path_text, img2_path_text],
                )
            except KeyboardInterrupt:
                print("\nexiting ...")
                break
            except Exception as exc:
                print(f"error: {exc}")
                import traceback

                traceback.print_exc()
    finally:
        await manager.stop_all()


if __name__ == "__main__":
    asyncio.run(main())
