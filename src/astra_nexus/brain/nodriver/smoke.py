from __future__ import annotations

import asyncio
from typing import Any

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.chatgpt_client import ChatGPTClient
from astra_nexus.brain.nodriver.dom_probe import collect_dom_probe, write_dom_probe_report
from astra_nexus.brain.nodriver.exceptions import (
    NoDriverPromptBoxNotFoundError,
    NoDriverProviderError,
)
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
    exit_code = 0
    error: NoDriverProviderError | None = None
    report_payload: dict[str, Any] | None = None
    report_path = None
    try:
        report_payload = await collect_dom_probe(session)
        report_path = write_dom_probe_report(settings, report_payload)
        print(f"ready_state: {report_payload.get('ready_state')}")
        print(f"textarea_count: {report_payload.get('textarea_count')}")
        print(f"contenteditable_count: {report_payload.get('contenteditable_count')}")
        print(f"textbox_count: {report_payload.get('textbox_count')}")
        print(f"candidate_count: {report_payload.get('candidate_count')}")
        print(f"dom_probe: {report_path}")
        result = await client.ask(SMOKE_PROMPT)
    except NoDriverProviderError as exc:
        error = exc
        exit_code = 1
        print(f"status: {exc.status}")
        print(f"stage: {exc.stage or 'unknown'}")
        print(f"message: {exc}")
        if exc.url:
            print(f"url: {exc.url}")
        if exc.selector:
            print(f"selector: {exc.selector}")
        if isinstance(exc, NoDriverPromptBoxNotFoundError):
            if report_payload is None:
                report_payload = {
                    **exc.details,
                    "current_url": exc.url,
                    "page_title": exc.page_title,
                }
                report_path = write_dom_probe_report(settings, report_payload)
            print(f"ready_state: {report_payload.get('ready_state')}")
            print(f"textarea_count: {report_payload.get('textarea_count')}")
            print(f"contenteditable_count: {report_payload.get('contenteditable_count')}")
            print(f"textbox_count: {report_payload.get('textbox_count')}")
            print(f"candidate_count: {report_payload.get('candidate_count')}")
            print(f"dom_probe: {report_path}")
        print(f"action: {exc.action}")
    finally:
        if error is not None and settings.nodriver_keep_browser_open_on_error:
            await asyncio.to_thread(
                input,
                "Браузер оставлен открытым. Проверь страницу и нажми Enter для закрытия.",
            )
        await session.stop()

    if exit_code:
        return exit_code
    print("status: ok")
    print(f"result: {result}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
