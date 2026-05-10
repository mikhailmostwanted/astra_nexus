from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from astra_nexus.brain.nodriver.evaluate import evaluate_value, unwrap_evaluate_result
from astra_nexus.brain.nodriver.exceptions import (
    NoDriverBrowserConnectError,
    NoDriverChromeStartTimeoutError,
    NoDriverDependencyError,
    NoDriverPageLoadError,
    NoDriverProfileLockedError,
)
from astra_nexus.brain.nodriver.lifecycle import NoDriverLifecycleManager
from astra_nexus.brain.nodriver.windowing import (
    build_nodriver_browser_args,
    effective_nodriver_headless,
)
from astra_nexus.config.settings import Settings

logger = logging.getLogger(__name__)


class BrowserSession:
    def __init__(
        self,
        settings: Settings,
        start_browser: Callable[..., Any] | None = None,
        lifecycle_context: str = "provider",
        lifecycle: NoDriverLifecycleManager | None = None,
    ) -> None:
        self.settings = settings
        self.lifecycle_context = lifecycle_context
        self.lifecycle = lifecycle or NoDriverLifecycleManager(
            settings,
            context=lifecycle_context,
        )
        self.user_data_dir = self.lifecycle.user_data_dir
        self._start_browser = start_browser
        self.browser: Any | None = None
        self.tab: Any | None = None

    async def start(self) -> Any:
        if self.browser is not None:
            return self.browser

        try:
            start_browser = self._start_browser or self._load_nodriver_start()
            kwargs = self.build_start_kwargs(start_browser)
        except Exception:
            raise

        last_error: Exception | None = None
        cleanup_left_profile_locked = False
        max_attempts = max(1, self.settings.nodriver_start_retry_attempts)
        for attempt in range(1, max_attempts + 1):
            previous_profile_process_pids: set[int] = set()
            try:
                self.lifecycle.acquire()
                previous_profile_process_pids = {
                    process.pid for process in self.lifecycle.inspect().live_profile_processes
                }
                logger.info(
                    "Запуск NoDriver browser, попытка %s/%s, profile: %s",
                    attempt,
                    max_attempts,
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
            except (KeyboardInterrupt, asyncio.CancelledError):
                self._cleanup_failed_start(previous_profile_process_pids)
                raise
            except NoDriverProfileLockedError:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning("NoDriver browser start failed: %s", exc)
                cleanup_report = self._cleanup_failed_start(previous_profile_process_pids)
                if cleanup_report.terminated_profile_processes:
                    logger.info(
                        "Terminated Chrome processes from failed NoDriver start: %s",
                        ", ".join(
                            str(process.pid)
                            for process in cleanup_report.terminated_profile_processes
                        ),
                    )
                if cleanup_report.removed_chrome_lock_files:
                    logger.info(
                        "Removed stale Chrome profile lock files after failed start: %s",
                        ", ".join(cleanup_report.removed_chrome_lock_files),
                    )
                if cleanup_report.live_profile_processes:
                    cleanup_left_profile_locked = True
                    break
                if attempt < max_attempts:
                    await asyncio.sleep(self._start_retry_backoff_seconds())

        if isinstance(last_error, TimeoutError):
            raise NoDriverChromeStartTimeoutError(
                timeout_seconds=self.settings.nodriver_start_timeout_seconds
            ) from last_error
        message = "Failed to connect to browser"
        if last_error is not None:
            message = f"{message}: {last_error}"
        action = (
            "выполни astra-nexus-nodriver-clean, закрой оставшийся Chrome PID "
            "и повтори astra-nexus-nodriver-smoke"
            if cleanup_left_profile_locked
            else "повтори команду; Astra Nexus уже очистил lock-файлы между попытками"
        )
        raise NoDriverBrowserConnectError(message, action=action) from last_error

    def build_start_kwargs(self, start_browser: Callable[..., Any] | None = None) -> dict[str, Any]:
        start_browser = start_browser or self._start_browser or self._load_nodriver_start()
        kwargs: dict[str, Any] = {
            "headless": effective_nodriver_headless(
                self.settings,
                context=self.lifecycle_context,
            ),
            "user_data_dir": str(self.user_data_dir),
        }
        browser_args = build_nodriver_browser_args(
            self.settings,
            context=self.lifecycle_context,
        )
        if browser_args and self._supports_kwarg(start_browser, "browser_args"):
            kwargs["browser_args"] = browser_args
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

    def _start_retry_backoff_seconds(self) -> float:
        return max(0.0, float(self.settings.nodriver_start_retry_backoff_seconds))

    def _cleanup_failed_start(self, previous_profile_process_pids: set[int]) -> Any:
        return self.lifecycle.cleanup_after_start_failure(
            previous_profile_process_pids=previous_profile_process_pids,
            terminate_grace_seconds=max(
                0.0,
                float(self.settings.nodriver_after_terminate_grace_seconds),
            ),
        )

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
        return await self.ensure_chatgpt_page()

    async def ensure_chatgpt_page(self, *, force_reload: bool = False) -> Any:
        if not force_reload and self.tab is not None:
            current_url = await self.current_url()
            if self._is_chatgpt_url(current_url):
                return self.tab
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

    async def current_url(self) -> str | None:
        if self.tab is None:
            return None
        value = getattr(self.tab, "url", None)
        value = unwrap_evaluate_result(value)
        if value:
            return str(value)
        try:
            value = await evaluate_value(self.tab, "window.location.href")
        except Exception:
            return None
        return str(value) if value else None

    async def current_title(self) -> str | None:
        if self.tab is None:
            return None
        value = getattr(self.tab, "title", None)
        value = unwrap_evaluate_result(value)
        if value:
            return str(value)
        try:
            value = await evaluate_value(self.tab, "document.title")
        except Exception:
            return None
        return str(value) if value else None

    def _is_chatgpt_url(self, url: str | None) -> bool:
        if not url:
            return False
        current = urlparse(url)
        expected = urlparse(self.settings.nodriver_chatgpt_url)
        expected_host = expected.hostname or "chatgpt.com"
        current_host = current.hostname or ""
        return current.scheme in {"http", "https"} and (
            current_host == expected_host or current_host.endswith(f".{expected_host}")
        )

    async def stop(self) -> None:
        if self.browser is None:
            self.lifecycle.release()
            return
        browser = self.browser
        try:
            await self._shutdown_browser(browser)
        finally:
            self.browser = None
            self.tab = None
            self.lifecycle.release()

    async def _shutdown_browser(self, browser: Any) -> None:
        for method_name in ("stop", "close"):
            method = getattr(browser, method_name, None)
            if method is None:
                continue
            try:
                result = method()
                if inspect.isawaitable(result):
                    await result
                return
            except Exception as exc:
                logger.warning("NoDriver browser %s failed: %s", method_name, exc)
