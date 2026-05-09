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
    session = getattr(getattr(provider, "client", None), "session", None)
    error: NoDriverProviderError | None = None

    try:
        response = await provider.ask(
            agent_id="manual",
            prompt=prompt,
            context={"task_prompt": prompt},
        )
    except NoDriverProviderError as exc:
        error = exc
        print(f"status: {exc.error_code}")
        print(f"stage: {exc.stage or 'unknown'}")
        print(f"message: {exc}")
        if exc.url:
            print(f"url: {exc.url}")
        if exc.selector:
            print(f"selector: {exc.selector}")
        for key in (
            "ready_state",
            "textarea_count",
            "contenteditable_count",
            "textbox_count",
            "candidate_count",
        ):
            if key in exc.details:
                print(f"{key}: {exc.details[key]}")
        print(f"action: {exc.action}")
        return_code = 1
    else:
        print("status: ok")
        print(f"response: {response.content}")
        return_code = 0
    finally:
        if error is not None and settings.nodriver_keep_browser_open_on_error and session:
            await asyncio.to_thread(
                input,
                "Браузер оставлен открытым. Проверь страницу и нажми Enter для закрытия.",
            )
        if session is not None:
            await session.stop()

    return return_code


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
