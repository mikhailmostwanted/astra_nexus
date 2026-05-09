from __future__ import annotations

import asyncio
import sys
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
    NoDriverProviderError,
)
from astra_nexus.config.settings import load_settings
from astra_nexus.utils.logging import configure_logging


async def run(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    prompt = " ".join(argv).strip()
    if not prompt:
        print('Использование: astra-nexus-nodriver-insert-probe "текст"')
        return 2

    settings = load_settings()
    configure_logging(settings.log_level)
    session = BrowserSession(settings, lifecycle_context="insert_probe")
    client = ChatGPTClient(settings, session=session)
    error: NoDriverProviderError | None = None

    try:
        payload = await collect_dom_probe(session)
        report_path = write_dom_probe_report(settings, payload)
        print(f"ready_state: {payload.get('ready_state')}")
        print(f"textarea_count: {payload.get('textarea_count')}")
        print(f"contenteditable_count: {payload.get('contenteditable_count')}")
        print(f"textbox_count: {payload.get('textbox_count')}")
        print(f"candidate_count: {payload.get('candidate_count')}")
        print(f"login_state: {payload.get('login_state')}")
        print(f"dom_probe: {report_path}")

        if is_evaluate_failed(payload):
            print("status: evaluate_failed")
            print("stage: dom.probe.evaluate")
            print("message: DOM probe не смог прочитать результат JavaScript")
            return 1
        if is_login_required(payload):
            raise NoDriverLoginRequiredError(
                "Нужен вход в ChatGPT.",
                stage="chatgpt.login.check.started",
                url=payload.get("current_url"),
                page_title=payload.get("page_title"),
                details=payload,
            )
        if not is_chatgpt_composer_ready(payload):
            raise NoDriverChatGPTUINotReadyError(
                "Интерфейс ChatGPT Web не готов: composer не найден.",
                stage="chatgpt.prompt_box.search.started",
                url=payload.get("current_url"),
                page_title=payload.get("page_title"),
                details=payload,
            )

        tab = getattr(session, "tab", None)
        if tab is None:
            tab = await session.ensure_chatgpt_page()
        details = await client._fill_prompt(tab, prompt)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("Остановлено пользователем.")
        return 130
    except NoDriverProviderError as exc:
        error = exc
        print(f"status: {exc.error_code}")
        print(f"stage: {exc.stage or 'unknown'}")
        print(f"message: {exc}")
        if exc.url:
            print(f"url: {exc.url}")
        if exc.selector:
            print(f"selector: {exc.selector}")
        for key in ("ready_state", "candidate_count", "login_state"):
            value = _detail_value(exc.details, key)
            if value is not None:
                print(f"{key}: {value}")
        print(f"action: {exc.action}")
        return 1
    finally:
        if error is not None and settings.nodriver_keep_browser_open_on_error:
            await asyncio.to_thread(
                input,
                "Браузер оставлен открытым. Проверь страницу и нажми Enter для закрытия.",
            )
        await session.stop()

    print("status: ok")
    print(f"selector: {details.get('selector')}")
    print(f"method: {details.get('method')}")
    print(f"textLength: {details.get('textLength')}")
    return 0


def _detail_value(details: dict[str, Any], key: str) -> Any:
    if key in details:
        return details[key]
    summary = details.get("dom_probe_summary")
    if isinstance(summary, dict):
        return summary.get(key)
    return None


def main() -> None:
    try:
        raise SystemExit(asyncio.run(run()))
    except KeyboardInterrupt:
        print("Остановлено пользователем.")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
