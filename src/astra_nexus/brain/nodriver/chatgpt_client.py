from __future__ import annotations

import asyncio
import logging
from typing import Any

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.exceptions import (
    NoDriverLoginRequiredError,
    NoDriverSelectorNotFoundError,
    NoDriverTimeoutError,
)
from astra_nexus.brain.nodriver.response_parser import parse_last_assistant_response
from astra_nexus.brain.nodriver.selectors import (
    ASSISTANT_MESSAGE_QUERY,
    LOGIN_REQUIRED_QUERY,
    PROMPT_INPUT_SELECTORS,
    SEND_BUTTON_SELECTORS,
    STOP_BUTTON_SELECTORS,
)
from astra_nexus.config.settings import Settings

logger = logging.getLogger(__name__)


class ChatGPTClient:
    def __init__(self, settings: Settings, session: BrowserSession | None = None) -> None:
        self.settings = settings
        self.session = session or BrowserSession(settings)

    async def ask(self, prompt: str) -> str:
        try:
            return await asyncio.wait_for(
                self._ask(prompt),
                timeout=self.settings.nodriver_response_timeout_seconds,
            )
        except TimeoutError as exc:
            raise NoDriverTimeoutError() from exc

    async def _ask(self, prompt: str) -> str:
        tab = await self.session.open_chatgpt()
        if await self._login_required(tab):
            raise NoDriverLoginRequiredError()

        before_count = len(await self._assistant_messages(tab))
        prompt_input = await self._first_selector(tab, PROMPT_INPUT_SELECTORS)
        await self._fill_prompt(prompt_input, prompt)
        send_button = await self._first_selector(tab, SEND_BUTTON_SELECTORS)
        await send_button.click()
        await self._wait_for_generation(tab, before_count)
        return parse_last_assistant_response(await self._assistant_messages(tab))

    async def _login_required(self, tab: Any) -> bool:
        try:
            return bool(await tab.evaluate(LOGIN_REQUIRED_QUERY))
        except Exception:
            return False

    async def _assistant_messages(self, tab: Any) -> list[str]:
        result = await tab.evaluate(ASSISTANT_MESSAGE_QUERY)
        return list(result or [])

    async def _first_selector(self, tab: Any, selectors: list[str]) -> Any:
        for selector in selectors:
            try:
                element = await tab.query_selector(selector)
            except Exception:
                element = None
            if element is not None:
                return element
        raise NoDriverSelectorNotFoundError(f"Не найден ни один selector: {', '.join(selectors)}")

    async def _fill_prompt(self, element: Any, prompt: str) -> None:
        if hasattr(element, "set_text"):
            await element.set_text(prompt)
            return
        if hasattr(element, "send_keys"):
            await element.send_keys(prompt)
            return
        raise NoDriverSelectorNotFoundError("Элемент prompt не поддерживает ввод текста.")

    async def _wait_for_generation(self, tab: Any, before_count: int) -> None:
        deadline = (
            asyncio.get_running_loop().time() + self.settings.nodriver_response_timeout_seconds
        )
        while asyncio.get_running_loop().time() < deadline:
            if len(
                await self._assistant_messages(tab)
            ) > before_count and not await self._has_stop_button(tab):
                return
            await asyncio.sleep(1)
        raise NoDriverTimeoutError()

    async def _has_stop_button(self, tab: Any) -> bool:
        for selector in STOP_BUTTON_SELECTORS:
            try:
                if await tab.query_selector(selector) is not None:
                    return True
            except Exception:
                continue
        return False
