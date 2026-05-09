from __future__ import annotations

import asyncio
import logging
from typing import Any

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.exceptions import (
    NoDriverLoginRequiredError,
    NoDriverPromptBoxNotFoundError,
    NoDriverProviderError,
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

    async def ask(
        self,
        prompt: str,
        *,
        debug_context: dict[str, Any] | None = None,
    ) -> str:
        debug_context = debug_context or {}
        try:
            return await asyncio.wait_for(
                self._ask(prompt, debug_context),
                timeout=self.settings.nodriver_response_timeout_seconds,
            )
        except TimeoutError as exc:
            raise NoDriverTimeoutError(
                stage="chatgpt.response.wait.started",
                url=await self.session.current_url(),
                page_title=await self.session.current_title(),
            ) from exc
        except NoDriverProviderError as exc:
            await self._enrich_error(exc)
            self._log_stage(
                "chatgpt.error",
                debug_context,
                error_code=exc.error_code,
                error_message=str(exc),
                url=exc.url,
            )
            raise

    async def _ask(self, prompt: str, debug_context: dict[str, Any]) -> str:
        self._log_stage("browser.session.ensure_started", debug_context)
        await self.session.start()

        self._log_stage("chatgpt.page.open", debug_context)
        tab = await self.session.ensure_chatgpt_page()
        self._log_stage("chatgpt.page.loaded", debug_context, url=await self.session.current_url())

        self._log_stage("chatgpt.login.check.started", debug_context)
        if await self._login_required(tab):
            raise NoDriverLoginRequiredError(
                stage="chatgpt.login.check.started",
                url=await self.session.current_url(),
                page_title=await self.session.current_title(),
                details=await self._page_diagnostics(tab),
            )
        self._log_stage("chatgpt.login.check.ok", debug_context)

        before_count = len(await self._assistant_messages(tab))
        self._log_stage("chatgpt.prompt_box.search.started", debug_context)
        prompt_input = await self._first_selector(
            tab,
            PROMPT_INPUT_SELECTORS,
            stage="chatgpt.prompt_box.search.started",
            kind="prompt_box",
        )
        self._log_stage("chatgpt.prompt_box.found", debug_context)

        self._log_stage("chatgpt.prompt.insert.started", debug_context)
        await self._fill_prompt(prompt_input, prompt)
        self._log_stage("chatgpt.prompt.insert.ok", debug_context)

        self._log_stage("chatgpt.send.started", debug_context)
        send_button = await self._first_selector(
            tab,
            SEND_BUTTON_SELECTORS,
            stage="chatgpt.send.started",
            kind="send_button",
        )
        await send_button.click()
        self._log_stage("chatgpt.send.ok", debug_context)

        self._log_stage("chatgpt.response.wait.started", debug_context)
        await self._wait_for_generation(tab, before_count)
        self._log_stage("chatgpt.response.wait.ok", debug_context)

        self._log_stage("chatgpt.response.parse.started", debug_context)
        try:
            response = parse_last_assistant_response(await self._assistant_messages(tab))
        except NoDriverSelectorNotFoundError as exc:
            exc.stage = exc.stage or "chatgpt.response.parse.started"
            raise
        self._log_stage("chatgpt.response.parse.ok", debug_context)
        return response

    async def _login_required(self, tab: Any) -> bool:
        try:
            return bool(await tab.evaluate(LOGIN_REQUIRED_QUERY))
        except Exception:
            return False

    async def _assistant_messages(self, tab: Any) -> list[str]:
        result = await tab.evaluate(ASSISTANT_MESSAGE_QUERY)
        return list(result or [])

    async def _first_selector(
        self,
        tab: Any,
        selectors: list[str],
        *,
        stage: str,
        kind: str,
    ) -> Any:
        for selector in selectors:
            try:
                element = await tab.query_selector(selector)
            except Exception:
                element = None
            if element is not None:
                return element

        message = f"Не найден ни один selector: {', '.join(selectors)}"
        kwargs = {
            "stage": stage,
            "url": await self.session.current_url(),
            "page_title": await self.session.current_title(),
            "selector": ", ".join(selectors),
            "details": await self._page_diagnostics(tab),
        }
        if kind == "prompt_box":
            raise NoDriverPromptBoxNotFoundError(
                "Поле ввода ChatGPT не найдено.",
                **kwargs,
            )
        raise NoDriverSelectorNotFoundError(message, **kwargs)

    async def _fill_prompt(self, element: Any, prompt: str) -> None:
        if hasattr(element, "set_text"):
            await element.set_text(prompt)
            return
        if hasattr(element, "send_keys"):
            await element.send_keys(prompt)
            return
        raise NoDriverPromptBoxNotFoundError(
            "Элемент prompt не поддерживает ввод текста.",
            stage="chatgpt.prompt.insert.started",
            url=await self.session.current_url(),
            page_title=await self.session.current_title(),
            selector=", ".join(PROMPT_INPUT_SELECTORS),
        )

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
        raise NoDriverTimeoutError(
            stage="chatgpt.response.wait.started",
            url=await self.session.current_url(),
            page_title=await self.session.current_title(),
        )

    async def _has_stop_button(self, tab: Any) -> bool:
        for selector in STOP_BUTTON_SELECTORS:
            try:
                if await tab.query_selector(selector) is not None:
                    return True
            except Exception:
                continue
        return False

    async def _enrich_error(self, exc: NoDriverProviderError) -> None:
        if exc.url is None:
            exc.url = await self.session.current_url()
        if exc.page_title is None:
            exc.page_title = await self.session.current_title()

    async def _page_diagnostics(self, tab: Any) -> dict[str, Any]:
        try:
            result = await tab.evaluate(
                """
(() => {
  const text = document.body ? document.body.innerText.toLowerCase() : '';
  const promptSelector = '#prompt-textarea,[contenteditable="true"],div.ProseMirror';
  const prompt = document.querySelector(promptSelector);
  const login = document.querySelector('a[href*="/auth/login"],button[data-testid="login-button"]');
  const verification =
    text.includes('verify you are human') ||
    text.includes('checking your browser') ||
    text.includes('cloudflare') ||
    Boolean(document.querySelector('[id*="challenge"], iframe[src*="challenges.cloudflare.com"]'));
  return {
    prompt_present: Boolean(prompt),
    login_present: Boolean(login),
    verification_present: Boolean(verification),
    app_root_present: Boolean(document.querySelector('main, [data-testid]')),
  };
})()
"""
            )
        except Exception:
            return {}
        return dict(result or {})

    def _log_stage(
        self,
        stage: str,
        debug_context: dict[str, Any],
        **extra: Any,
    ) -> None:
        logger.info(
            stage,
            extra={
                "task_id": debug_context.get("task_id"),
                "run_id": debug_context.get("run_id"),
                "agent_id": debug_context.get("agent_id"),
                "stage": stage,
                **extra,
            },
        )
