import asyncio
import sys

import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conf.channel import CHANNEL_CONFIG
from src.channels import ChannelManager
from src.channels.telegram import TelegramChannel
from src.runtime import CreativeClawRuntime


async def main() -> None:
    """Start the Telegram channel runner."""
    runtime = CreativeClawRuntime()
    manager = ChannelManager(runtime)
    telegram = TelegramChannel(
        token=CHANNEL_CONFIG.telegram.bot_token,
        allow_from=CHANNEL_CONFIG.telegram.allow_from,
        inbound_handler=manager.handle_inbound,
    )
    manager.register(telegram)
    await manager.start_all()
    print("Telegram channel is running. Press Ctrl+C to stop.")
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        print("\nStopping Telegram channel ...")
    finally:
        await manager.stop_all()


if __name__ == "__main__":
    asyncio.run(main())
