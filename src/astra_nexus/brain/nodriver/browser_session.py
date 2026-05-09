from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from astra_nexus.brain.nodriver.exceptions import (
    NoDriverBrowserConnectError,
    NoDriverDependencyError,
    NoDriverPageLoadError,
)
from astra_nexus.config.settings import Settings

logger = logging.getLogger(__name__)


class BrowserSession:
    def __init__(
        self,
        settings: Settings,
        start_browser: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self.user_data_dir = Path(settings.nodriver_user_data_dir).expanduser().resolve()
        self._start_browser = start_browser
        self.browser: Any | None = None
        self.tab: Any | None = None

    async def start(self) -> Any:
        if self.browser is not None:
            return self.browser

        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        start_browser = self._start_browser or self._load_nodriver_start()
        kwargs = self.build_start_kwargs(start_browser)

        last_error: Exception | None = None
        for attempt in range(1, self.settings.nodriver_start_retry_attempts + 1):
            try:
                logger.info(
                    "Запуск NoDriver browser, попытка %s/%s, profile: %s",
                    attempt,
                    self.settings.nodriver_start_retry_attempts,
                    self.user_data_dir,
                )
                browser = start_browser(**kwargs)
                if inspect.isawaitable(browser):
                    browser = await asyncio.wait_for(
                        browser,
                        timeout=self.settings.nodriver_start_timeout_seconds,
                    )
                self.browser = browser
                return self.browser
            except Exception as exc:
                last_error = exc
                logger.warning("NoDriver browser start failed: %s", exc)
                if attempt < self.settings.nodriver_start_retry_attempts:
                    await asyncio.sleep(self.settings.nodriver_start_retry_delay_seconds)

        message = "Failed to connect to browser"
        if last_error is not None:
            message = f"{message}: {last_error}"
        raise NoDriverBrowserConnectError(message) from last_error

    def build_start_kwargs(self, start_browser: Callable[..., Any] | None = None) -> dict[str, Any]:
        start_browser = start_browser or self._start_browser or self._load_nodriver_start()
        kwargs: dict[str, Any] = {
            "headless": self.settings.nodriver_headless,
            "user_data_dir": str(self.user_data_dir),
        }
        browser_executable_path = self.settings.nodriver_browser_executable_path
        if browser_executable_path is not None:
            kwargs["browser_executable_path"] = str(
                Path(browser_executable_path).expanduser().resolve()
            )
        if self._supports_kwarg(start_browser, "start_timeout"):
            kwargs["start_timeout"] = self.settings.nodriver_start_timeout_seconds
        if self._has_explicit_kwarg(start_browser, "no_sandbox"):
            kwargs["no_sandbox"] = self.settings.nodriver_no_sandbox
        elif self._has_explicit_kwarg(start_browser, "sandbox"):
            kwargs["sandbox"] = not self.settings.nodriver_no_sandbox
        elif self._supports_kwarg(start_browser, "no_sandbox"):
            kwargs["no_sandbox"] = self.settings.nodriver_no_sandbox
        return kwargs

    def _load_nodriver_start(self) -> Callable[..., Any]:
        try:
            import nodriver as uc
        except ImportError as exc:
            raise NoDriverDependencyError() from exc
        return uc.start

    def _supports_kwarg(self, func: Callable[..., Any], name: str) -> bool:
        signature = inspect.signature(func)
        return name in signature.parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )

    def _has_explicit_kwarg(self, func: Callable[..., Any], name: str) -> bool:
        return name in inspect.signature(func).parameters

    async def open_chatgpt(self) -> Any:
        return await self.open_url(self.settings.nodriver_chatgpt_url)

    async def open_url(self, url: str) -> Any:
        browser = await self.start()
        try:
            self.tab = await asyncio.wait_for(
                browser.get(url),
                timeout=self.settings.nodriver_page_load_timeout_seconds,
            )
            return self.tab
        except TimeoutError as exc:
            raise NoDriverPageLoadError("Истекло время загрузки ChatGPT Web.") from exc
        except Exception as exc:
            raise NoDriverPageLoadError() from exc

    async def stop(self) -> None:
        if self.browser is None:
            return
        try:
            self.browser.stop()
        finally:
            self.browser = None
            self.tab = None
