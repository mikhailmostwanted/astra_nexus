from __future__ import annotations

import asyncio

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.chatgpt_client import ChatGPTClient
from astra_nexus.brain.nodriver.exceptions import NoDriverProviderError
from astra_nexus.config.settings import load_settings
from astra_nexus.utils.logging import configure_logging

SMOKE_PROMPT = "Ответь одним предложением: Astra Nexus online."


async def amain() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    session = BrowserSession(settings, lifecycle_context="smoke")
    client = ChatGPTClient(settings, session=session)

    print("Astra Nexus NoDriver smoke")
    print(f"Browser profile: {session.user_data_dir}")
    try:
        result = await client.ask(SMOKE_PROMPT)
    except NoDriverProviderError as exc:
        print(f"status: {exc.status}")
        print(f"message: {exc}")
        print(f"action: {exc.action}")
        return 1
    finally:
        await session.stop()

    print("status: ok")
    print(f"result: {result}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
