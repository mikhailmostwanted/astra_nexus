from __future__ import annotations

import asyncio
import logging

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.config.settings import load_settings
from astra_nexus.utils.logging import configure_logging

logger = logging.getLogger(__name__)


async def amain() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    session = BrowserSession(settings)
    await session.open_chatgpt()
    print("Astra Nexus NoDriver login")
    print(f"Browser profile: {settings.nodriver_user_data_dir}")
    print("Вручную авторизуйся в ChatGPT в открытом браузере.")
    print("После успешного входа нажми Ctrl+C в этом терминале.")
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        logger.info("Ручная подготовка NoDriver profile остановлена пользователем.")
    finally:
        await session.stop()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
