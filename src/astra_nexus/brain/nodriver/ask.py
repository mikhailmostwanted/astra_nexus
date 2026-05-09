from __future__ import annotations

import asyncio
import sys
from typing import Any

from astra_nexus.brain.nodriver.exceptions import NoDriverProviderError
from astra_nexus.brain.nodriver_provider import NoDriverProvider
from astra_nexus.config.settings import load_settings
from astra_nexus.utils.logging import configure_logging


async def run(argv: list[str] | None = None, *, provider: Any | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    prompt = " ".join(argv).strip()
    if not prompt:
        print('Использование: astra-nexus-nodriver-ask "текст"')
        return 2

    settings = load_settings()
    configure_logging(settings.log_level)
    provider = provider or NoDriverProvider(settings=settings)

    try:
        response = await provider.ask(
            agent_id="manual",
            prompt=prompt,
            context={"task_prompt": prompt},
        )
    except NoDriverProviderError as exc:
        print(f"status: {exc.error_code}")
        print(f"stage: {exc.stage or 'unknown'}")
        print(f"message: {exc}")
        if exc.url:
            print(f"url: {exc.url}")
        if exc.selector:
            print(f"selector: {exc.selector}")
        print(f"action: {exc.action}")
        return 1

    print("status: ok")
    print(f"response: {response.content}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
