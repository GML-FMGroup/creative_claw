import asyncio
import sys

import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conf.channel import CHANNEL_CONFIG
from src.channels import ChannelManager
from src.channels.feishu import FeishuChannel
from src.runtime import CreativeClawRuntime


async def main() -> None:
    """Start the Feishu channel runner."""
    runtime = CreativeClawRuntime()
    manager = ChannelManager(runtime)
    feishu = FeishuChannel(
        app_id=CHANNEL_CONFIG.feishu.app_id,
        app_secret=CHANNEL_CONFIG.feishu.app_secret,
        encrypt_key=CHANNEL_CONFIG.feishu.encrypt_key,
        verification_token=CHANNEL_CONFIG.feishu.verification_token,
        allow_from=CHANNEL_CONFIG.feishu.allow_from,
        inbound_handler=manager.handle_inbound,
    )
    manager.register(feishu)
    await manager.start_all()
    print("Feishu channel is running. Press Ctrl+C to stop.")
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        print("\nStopping Feishu channel ...")
    finally:
        await manager.stop_all()


if __name__ == "__main__":
    asyncio.run(main())
