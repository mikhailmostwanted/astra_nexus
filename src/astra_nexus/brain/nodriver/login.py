from __future__ import annotations

import asyncio
import logging

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.dom_probe import (
    collect_dom_probe,
    is_chatgpt_composer_ready,
    is_login_required,
    write_dom_probe_report,
)
from astra_nexus.brain.nodriver.exceptions import NoDriverProviderError
from astra_nexus.config.settings import load_settings
from astra_nexus.utils.logging import configure_logging

logger = logging.getLogger(__name__)


async def amain() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    session = BrowserSession(settings, lifecycle_context="login")
    print("Astra Nexus NoDriver login")
    print(f"Browser profile: {session.user_data_dir}")
    try:
        await session.open_chatgpt()
        await asyncio.to_thread(
            input,
            "Войди в ChatGPT в открывшемся окне. Когда увидишь поле ввода ChatGPT, нажми Enter.",
        )
        payload = await collect_dom_probe(session)
        report_path = write_dom_probe_report(settings, payload)
        print(f"ready_state: {payload.get('ready_state')}")
        print(f"textarea_count: {payload.get('textarea_count')}")
        print(f"contenteditable_count: {payload.get('contenteditable_count')}")
        print(f"textbox_count: {payload.get('textbox_count')}")
        print(f"login_buttons_count: {payload.get('login_buttons_count')}")
        print(f"candidate_count: {payload.get('candidate_count')}")
        print(f"login_state: {payload.get('login_state')}")
        print(f"dom_probe: {report_path}")
        if is_chatgpt_composer_ready(payload):
            print("status: ok")
            print("message: browser profile сохранён, поле ввода ChatGPT найдено")
            return 0
        if is_login_required(payload):
            print("status: login_required")
            print("message: вход в ChatGPT не подтверждён")
        else:
            print("status: chatgpt_ui_not_ready")
            print("message: поле ввода ChatGPT не найдено")
        if settings.nodriver_keep_browser_open_on_error:
            await asyncio.to_thread(
                input,
                "Браузер оставлен открытым. Проверь страницу и нажми Enter для закрытия.",
            )
        return 1
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Ручная подготовка NoDriver profile остановлена пользователем.")
        print("Остановлено пользователем.")
        return 130
    except NoDriverProviderError as exc:
        print(f"status: {exc.status}")
        print(f"message: {exc}")
        print(f"action: {exc.action}")
        if settings.nodriver_keep_browser_open_on_error:
            await asyncio.to_thread(
                input,
                "Браузер оставлен открытым. Проверь страницу и нажми Enter для закрытия.",
            )
        return 1
    finally:
        await session.stop()


def main() -> None:
    try:
        raise SystemExit(asyncio.run(amain()))
    except KeyboardInterrupt:
        print("Остановлено пользователем.")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
