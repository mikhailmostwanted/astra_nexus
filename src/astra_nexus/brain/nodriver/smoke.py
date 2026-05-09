from __future__ import annotations

import asyncio
from typing import Any

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.chatgpt_client import ChatGPTClient
from astra_nexus.brain.nodriver.dom_probe import (
    collect_dom_probe,
    is_chatgpt_composer_ready,
    is_evaluate_failed,
    is_login_required,
    write_dom_probe_report,
)
from astra_nexus.brain.nodriver.exceptions import (
    NoDriverChatGPTUINotReadyError,
    NoDriverLoginRequiredError,
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
        print(f"login_buttons_count: {report_payload.get('login_buttons_count')}")
        print(f"candidate_count: {report_payload.get('candidate_count')}")
        print(f"login_state: {report_payload.get('login_state')}")
        print(f"dom_probe: {report_path}")
        if is_evaluate_failed(report_payload):
            print("status: evaluate_failed")
            print("stage: dom.probe.evaluate")
            print("message: DOM probe не смог прочитать результат JavaScript")
            exception = report_payload.get("exception") or {}
            if isinstance(exception, dict):
                print(f"exception_type: {exception.get('type')}")
            return 1
        if is_login_required(report_payload):
            raise NoDriverLoginRequiredError(
                "Нужен вход в ChatGPT.",
                stage="chatgpt.login.check.started",
                url=report_payload.get("current_url"),
                page_title=report_payload.get("page_title"),
                details=report_payload,
            )
        if not is_chatgpt_composer_ready(report_payload):
            raise NoDriverChatGPTUINotReadyError(
                "Интерфейс ChatGPT Web не готов: composer не найден.",
                stage="chatgpt.prompt_box.search.started",
                url=report_payload.get("current_url"),
                page_title=report_payload.get("page_title"),
                details=report_payload,
            )
        result = await client.ask(SMOKE_PROMPT)
    except (KeyboardInterrupt, asyncio.CancelledError):
        exit_code = 130
        print("Остановлено пользователем.")
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
            print(f"login_buttons_count: {report_payload.get('login_buttons_count')}")
            print(f"candidate_count: {report_payload.get('candidate_count')}")
            print(f"login_state: {report_payload.get('login_state')}")
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
    try:
        raise SystemExit(asyncio.run(amain()))
    except KeyboardInterrupt:
        print("Остановлено пользователем.")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
