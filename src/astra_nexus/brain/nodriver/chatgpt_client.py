from __future__ import annotations

import asyncio
import logging
from typing import Any

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.dom_probe import (
    LOGIN_STATE_PROBE_SCRIPT,
    build_prompt_candidate_probe_script,
)
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
        login_state = await self._login_state(tab)
        if login_state.get("login_required"):
            raise NoDriverLoginRequiredError(
                stage="chatgpt.login.check.started",
                url=await self.session.current_url(),
                page_title=await self.session.current_title(),
                details=await self._page_diagnostics(tab, login_state=login_state),
            )
        if login_state.get("login_ok"):
            self._log_stage(
                "chatgpt.login.check.ok",
                debug_context,
                reason=login_state.get("reason"),
            )
        else:
            self._log_stage(
                "chatgpt.login.check.unknown",
                debug_context,
                reason=login_state.get("reason"),
            )

        before_count = len(await self._assistant_messages(tab))
        self._log_stage("chatgpt.prompt_box.search.started", debug_context)
        prompt_input = await self._wait_for_prompt_box(tab, debug_context, login_state)

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

    async def _login_state(self, tab: Any) -> dict[str, Any]:
        try:
            result = await tab.evaluate(LOGIN_STATE_PROBE_SCRIPT)
        except Exception:
            return {"login_required": False, "login_ok": False, "reason": "probe_failed"}
        return dict(result or {})

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

    async def _wait_for_prompt_box(
        self,
        tab: Any,
        debug_context: dict[str, Any],
        login_state: dict[str, Any],
    ) -> Any:
        self._log_stage("chatgpt.ui.wait.started", debug_context)
        deadline = (
            asyncio.get_running_loop().time() + self.settings.nodriver_page_load_timeout_seconds
        )
        attempts = 0
        last_summary: dict[str, Any] = {}

        while asyncio.get_running_loop().time() <= deadline:
            attempts += 1
            ready_state = await self._ready_state(tab)
            self._log_stage(
                "chatgpt.ui.wait.ready_state",
                debug_context,
                ready_state=ready_state,
                attempts=attempts,
            )
            if ready_state != "complete":
                await self._sleep_until_next_attempt(deadline)
                continue

            last_summary = await self._prompt_candidate_summary(tab)
            marked_selector = last_summary.get("marked_selector")
            if marked_selector:
                element = await tab.query_selector(str(marked_selector))
                if element is not None:
                    self._log_stage(
                        "chatgpt.prompt_box.found",
                        debug_context,
                        attempts=attempts,
                        candidate_count=last_summary.get("candidate_count"),
                    )
                    return element

            self._log_stage(
                "chatgpt.prompt_box.search.retry",
                debug_context,
                attempts=attempts,
                ready_state=ready_state,
                candidate_count=last_summary.get("candidate_count", 0),
            )
            await self._sleep_until_next_attempt(deadline)

        details = {
            **last_summary,
            "selectors_tried": PROMPT_INPUT_SELECTORS,
            "login_state": login_state,
            "attempts": attempts,
        }
        raise NoDriverPromptBoxNotFoundError(
            "Поле ввода ChatGPT не найдено.",
            stage="chatgpt.prompt_box.search.started",
            url=await self.session.current_url(),
            page_title=await self.session.current_title(),
            selector=", ".join(PROMPT_INPUT_SELECTORS),
            details=details,
        )

    async def _ready_state(self, tab: Any) -> str:
        try:
            value = await tab.evaluate("document.readyState")
        except Exception:
            return "unknown"
        return str(value or "unknown")

    async def _prompt_candidate_summary(self, tab: Any) -> dict[str, Any]:
        try:
            result = await tab.evaluate(build_prompt_candidate_probe_script())
        except Exception:
            return {
                "ready_state": "unknown",
                "textarea_count": 0,
                "contenteditable_count": 0,
                "textbox_count": 0,
                "candidate_count": 0,
                "visible_candidates": [],
                "marked_selector": None,
            }
        return dict(result or {})

    async def _sleep_until_next_attempt(self, deadline: float) -> None:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return
        await asyncio.sleep(min(0.5, remaining))

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

    async def _page_diagnostics(
        self,
        tab: Any,
        *,
        login_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            result = await tab.evaluate(build_prompt_candidate_probe_script())
        except Exception:
            result = {}
        diagnostics = dict(result or {})
        if login_state is not None:
            diagnostics["login_state"] = login_state
        return diagnostics

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
