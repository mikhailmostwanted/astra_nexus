from __future__ import annotations

import asyncio
import inspect
import json
import logging
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import contextmanager
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
    apply_macos_window_adjustment,
    build_nodriver_browser_args,
    effective_nodriver_headless,
    effective_nodriver_window_mode,
)
from astra_nexus.config.settings import Settings

logger = logging.getLogger(__name__)
REMOTE_DEBUGGING_HOST = "127.0.0.1"
REMOTE_DEBUGGING_VERSION_PATH = "/json/version"


def _read_remote_debugging_version(url: str) -> dict[str, object]:
    try:
        with urllib.request.urlopen(url, timeout=0.5) as response:
            raw_payload = response.read().decode("utf-8")
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        return {"open": False, "url": url, "error": str(exc)}

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        return {"open": False, "url": url, "error": f"invalid json: {exc}"}
    if not isinstance(payload, dict):
        return {"open": False, "url": url, "error": "unexpected json payload"}
    websocket_url = payload.get("webSocketDebuggerUrl")
    return {
        "open": bool(websocket_url),
        "url": url,
        "browser": payload.get("Browser"),
        "protocol_version": payload.get("Protocol-Version"),
        "web_socket_debugger_url": websocket_url,
    }


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
        self.start_diagnostics: list[dict[str, Any]] = []
        self._recovered_browser_previous_profile_process_pids: set[int] | None = None

    async def start(self) -> Any:
        if self.browser is not None:
            return self.browser

        try:
            start_browser = self._start_browser or self._load_nodriver_start()
        except Exception:
            raise

        last_error: Exception | None = None
        cleanup_left_profile_locked = False
        max_attempts = max(1, self.settings.nodriver_start_retry_attempts)
        fallback_window_mode = False
        self.start_diagnostics = []
        for attempt in range(1, max_attempts + 1):
            previous_profile_process_pids: set[int] = set()
            kwargs: dict[str, Any] = {}
            attempt_diagnostics: dict[str, Any] = {}
            try:
                remote_debugging_host = REMOTE_DEBUGGING_HOST
                remote_debugging_port = self._select_remote_debugging_port()
                kwargs = self.build_start_kwargs(
                    start_browser,
                    fallback_window_mode=fallback_window_mode,
                )
                attempt_diagnostics = self._build_attempt_diagnostics(
                    attempt=attempt,
                    max_attempts=max_attempts,
                    kwargs=kwargs,
                    fallback_window_mode=fallback_window_mode,
                    remote_debugging_host=remote_debugging_host,
                    remote_debugging_port=remote_debugging_port,
                )
                self.start_diagnostics.append(attempt_diagnostics)
                self.lifecycle.acquire()
                previous_profile_process_pids = {
                    process.pid for process in self.lifecycle.inspect().live_profile_processes
                }
                logger.info(
                    "Запуск NoDriver browser, попытка %s/%s, profile: %s, "
                    "remote_debugging_port: %s, window_mode: %s, chrome_args: %s",
                    attempt,
                    max_attempts,
                    self.user_data_dir,
                    attempt_diagnostics.get("remote_debugging_port"),
                    attempt_diagnostics.get("window_mode"),
                    attempt_diagnostics.get("chrome_args"),
                )
                with self._force_nodriver_free_port(start_browser, remote_debugging_port):
                    browser = start_browser(**kwargs)
                    if inspect.isawaitable(browser):
                        browser = await asyncio.wait_for(
                            browser,
                            timeout=self.settings.nodriver_start_timeout_seconds,
                        )
                self.browser = browser
                self._update_chrome_process_diagnostics(
                    attempt_diagnostics,
                    previous_profile_process_pids=previous_profile_process_pids,
                )
                await self._record_current_endpoint_diagnostics(attempt_diagnostics)
                try:
                    await self._apply_post_start_window_behavior(browser)
                except Exception as exc:
                    attempt_diagnostics["window_adjustment"] = {
                        "attempted": True,
                        "ok": False,
                        "error": str(exc),
                    }
                    logger.warning("NoDriver post-start window adjustment failed: %s", exc)
                attempt_diagnostics["connected"] = True
                return self.browser
            except (KeyboardInterrupt, asyncio.CancelledError):
                self._cleanup_failed_start(previous_profile_process_pids)
                raise
            except NoDriverProfileLockedError:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning("NoDriver browser start failed: %s", exc)
                self._update_chrome_process_diagnostics(
                    attempt_diagnostics,
                    previous_profile_process_pids=previous_profile_process_pids,
                )
                recovered_browser = await self._recover_after_early_connect_failure(
                    start_browser=start_browser,
                    exc=exc,
                    attempt_diagnostics=attempt_diagnostics,
                    previous_profile_process_pids=previous_profile_process_pids,
                )
                if recovered_browser is not None:
                    self.browser = recovered_browser
                    return self.browser
                cleanup_report = self._cleanup_failed_start(previous_profile_process_pids)
                self._record_cleanup_report(attempt_diagnostics, cleanup_report)
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
                if self._should_enable_window_fallback(
                    exc,
                    fallback_window_mode=fallback_window_mode,
                    kwargs=kwargs,
                ):
                    fallback_window_mode = True
                    logger.warning(
                        "NoDriver fallback window mode enabled after browser connect failure; "
                        "next start will avoid window-position/window-size/background flags"
                    )
                if attempt < max_attempts:
                    await asyncio.sleep(self._start_retry_backoff_seconds())

        if isinstance(last_error, TimeoutError):
            raise NoDriverChromeStartTimeoutError(
                timeout_seconds=self.settings.nodriver_start_timeout_seconds
            ) from last_error
        message = self._browser_connect_failure_message()
        if last_error is not None:
            message = f"{message}: {last_error}"
        action = (
            "выполни astra-nexus-nodriver-clean, закрой оставшийся Chrome PID "
            "и запусти astra-nexus-nodriver-doctor; затем повтори astra-nexus-nodriver-smoke"
            if cleanup_left_profile_locked
            else (
                "запусти astra-nexus-nodriver-doctor; если профиль занят, выполни "
                "astra-nexus-nodriver-clean; если нужен вход, выполни "
                "astra-nexus-nodriver-login; проверь Chrome и "
                "NODRIVER_USER_DATA_DIR, затем повтори astra-nexus-nodriver-smoke"
            )
        )
        raise NoDriverBrowserConnectError(
            message,
            action=action,
            details={
                "attempts": self.start_diagnostics,
                "settings": self._settings_diagnostics(),
            },
        ) from last_error

    def build_start_kwargs(
        self,
        start_browser: Callable[..., Any] | None = None,
        *,
        fallback_window_mode: bool = False,
        remote_debugging_host: str = REMOTE_DEBUGGING_HOST,
        remote_debugging_port: int | None = None,
        connect_existing: bool = False,
    ) -> dict[str, Any]:
        start_browser = start_browser or self._start_browser or self._load_nodriver_start()
        kwargs: dict[str, Any] = {
            "headless": effective_nodriver_headless(
                self.settings,
                context=self.lifecycle_context,
                fallback_window_mode=fallback_window_mode,
            ),
            "user_data_dir": str(self.user_data_dir),
        }
        browser_args = build_nodriver_browser_args(
            self.settings,
            context=self.lifecycle_context,
            fallback_window_mode=fallback_window_mode,
        )
        if browser_args and self._supports_kwarg(start_browser, "browser_args"):
            kwargs["browser_args"] = browser_args
        if connect_existing and self._supports_kwarg(start_browser, "host"):
            kwargs["host"] = remote_debugging_host
        if connect_existing and self._supports_kwarg(start_browser, "port"):
            kwargs["port"] = remote_debugging_port or self._select_remote_debugging_port()
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

    async def _recover_after_early_connect_failure(
        self,
        *,
        start_browser: Callable[..., Any],
        exc: Exception,
        attempt_diagnostics: dict[str, Any],
        previous_profile_process_pids: set[int],
    ) -> Any | None:
        if not self._is_browser_connect_error(exc):
            return None
        host = attempt_diagnostics.get("remote_debugging_host")
        port = attempt_diagnostics.get("remote_debugging_port")
        if not isinstance(host, str) or not isinstance(port, int):
            return None
        if not attempt_diagnostics.get("chrome_process_started"):
            attempt_diagnostics["endpoint_skipped_reason"] = "chrome_process_not_found"
            return None
        if not self._debugging_port_seen_in_chrome_command(attempt_diagnostics, port):
            attempt_diagnostics["endpoint_skipped_reason"] = (
                "remote_debugging_port_not_seen_in_chrome_command"
            )
            return None

        endpoint_report = await self._wait_for_remote_debugging_endpoint(
            host=host,
            port=port,
            timeout_seconds=float(self.settings.nodriver_start_timeout_seconds),
        )
        self._record_endpoint_report(attempt_diagnostics, endpoint_report)
        logger.info(
            "NoDriver remote debugging endpoint check: port=%s, open=%s, waited=%.2fs",
            port,
            endpoint_report.get("open"),
            endpoint_report.get("waited_seconds") or 0.0,
        )
        if not endpoint_report.get("open"):
            return None

        try:
            reconnect_kwargs = self.build_start_kwargs(
                start_browser,
                fallback_window_mode=True,
                remote_debugging_host=host,
                remote_debugging_port=port,
                connect_existing=True,
            )
            browser = start_browser(**reconnect_kwargs)
            if inspect.isawaitable(browser):
                browser = await asyncio.wait_for(
                    browser,
                    timeout=self.settings.nodriver_start_timeout_seconds,
                )
            attempt_diagnostics["connected"] = True
            attempt_diagnostics["reconnected_to_existing_browser"] = True
            self._recovered_browser_previous_profile_process_pids = set(
                previous_profile_process_pids
            )
            return browser
        except Exception as reconnect_exc:
            attempt_diagnostics["reconnect_error"] = str(reconnect_exc)
            logger.warning("NoDriver reconnect to ready browser endpoint failed: %s", reconnect_exc)
            return None

    async def _wait_for_remote_debugging_endpoint(
        self,
        *,
        host: str,
        port: int,
        timeout_seconds: float,
    ) -> dict[str, object]:
        started_at = time.monotonic()
        deadline = started_at + max(0.0, timeout_seconds)
        url = f"http://{host}:{port}{REMOTE_DEBUGGING_VERSION_PATH}"
        attempts = 0
        last_error = ""
        while True:
            attempts += 1
            report = await asyncio.to_thread(_read_remote_debugging_version, url)
            if report["open"]:
                report["attempts"] = attempts
                report["waited_seconds"] = round(time.monotonic() - started_at, 3)
                return report
            last_error = str(report.get("error") or "")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {
                    "open": False,
                    "url": url,
                    "attempts": attempts,
                    "waited_seconds": round(time.monotonic() - started_at, 3),
                    "error": last_error,
                }
            await asyncio.sleep(min(0.25, remaining))

    def _build_attempt_diagnostics(
        self,
        *,
        attempt: int,
        max_attempts: int,
        kwargs: dict[str, Any],
        fallback_window_mode: bool,
        remote_debugging_host: str,
        remote_debugging_port: int,
    ) -> dict[str, Any]:
        browser_args = list(kwargs.get("browser_args") or [])
        window_mode = effective_nodriver_window_mode(
            self.settings,
            context=self.lifecycle_context,
            fallback_window_mode=fallback_window_mode,
        )
        return {
            "attempt": attempt,
            "max_attempts": max_attempts,
            "context": self.lifecycle_context,
            "user_data_dir": str(self.user_data_dir),
            "remote_debugging_host": remote_debugging_host,
            "remote_debugging_port": remote_debugging_port,
            "browser_executable_path": kwargs.get("browser_executable_path"),
            "headless": bool(kwargs.get("headless")),
            "window_mode": window_mode,
            "requested_window_mode": self.settings.nodriver_window_mode,
            "minimal_args_mode": not browser_args and not bool(kwargs.get("headless")),
            "chrome_args": browser_args,
            "start_timeout_seconds": self.settings.nodriver_start_timeout_seconds,
            "retry_delay_seconds": self.settings.nodriver_start_retry_backoff_seconds,
            "after_terminate_grace_seconds": (self.settings.nodriver_after_terminate_grace_seconds),
            "background_start": self.settings.nodriver_background_start,
            "disable_focus_stealing": self.settings.nodriver_disable_focus_stealing,
            "chrome_process_started": False,
            "endpoint_open": False,
            "endpoint_waited_seconds": 0.0,
            "connected": False,
            "reconnected_to_existing_browser": False,
        }

    def _update_chrome_process_diagnostics(
        self,
        attempt_diagnostics: dict[str, Any],
        *,
        previous_profile_process_pids: set[int],
    ) -> None:
        snapshot = self.lifecycle.inspect()
        live_processes = snapshot.live_profile_processes
        new_processes = [
            process
            for process in live_processes
            if process.pid not in previous_profile_process_pids
        ]
        process_payload = [
            {"pid": process.pid, "command": process.command} for process in live_processes
        ]
        new_process_payload = [
            {"pid": process.pid, "command": process.command} for process in new_processes
        ]
        attempt_diagnostics["chrome_process_started"] = bool(new_processes or live_processes)
        attempt_diagnostics["chrome_processes"] = process_payload
        attempt_diagnostics["new_chrome_processes"] = new_process_payload
        attempt_diagnostics["applied_chrome_commands"] = [
            process.command for process in (new_processes or live_processes)
        ]

    def _record_endpoint_report(
        self,
        attempt_diagnostics: dict[str, Any],
        endpoint_report: dict[str, object],
    ) -> None:
        attempt_diagnostics["endpoint_open"] = bool(endpoint_report.get("open"))
        attempt_diagnostics["endpoint_waited_seconds"] = endpoint_report.get(
            "waited_seconds",
            0.0,
        )
        attempt_diagnostics["endpoint_url"] = endpoint_report.get("url")
        attempt_diagnostics["endpoint_attempts"] = endpoint_report.get("attempts")
        if endpoint_report.get("web_socket_debugger_url"):
            attempt_diagnostics["web_socket_debugger_url"] = endpoint_report[
                "web_socket_debugger_url"
            ]
        if endpoint_report.get("error"):
            attempt_diagnostics["endpoint_error"] = endpoint_report["error"]

    async def _record_current_endpoint_diagnostics(
        self,
        attempt_diagnostics: dict[str, Any],
    ) -> None:
        host = attempt_diagnostics.get("remote_debugging_host")
        port = attempt_diagnostics.get("remote_debugging_port")
        if not isinstance(host, str) or not isinstance(port, int):
            return
        endpoint_report = await self._wait_for_remote_debugging_endpoint(
            host=host,
            port=port,
            timeout_seconds=0,
        )
        self._record_endpoint_report(attempt_diagnostics, endpoint_report)

    def _record_cleanup_report(
        self,
        attempt_diagnostics: dict[str, Any],
        cleanup_report: Any,
    ) -> None:
        attempt_diagnostics["terminated_profile_processes"] = [
            {"pid": process.pid, "command": process.command}
            for process in cleanup_report.terminated_profile_processes
        ]
        attempt_diagnostics["removed_chrome_lock_files"] = list(
            cleanup_report.removed_chrome_lock_files
        )
        attempt_diagnostics["cleanup_live_profile_processes"] = [
            {"pid": process.pid, "command": process.command}
            for process in cleanup_report.live_profile_processes
        ]

    def _should_enable_window_fallback(
        self,
        exc: Exception,
        *,
        fallback_window_mode: bool,
        kwargs: dict[str, Any],
    ) -> bool:
        if fallback_window_mode:
            return False
        if not self._is_browser_connect_error(exc):
            return False
        return bool(
            kwargs.get("browser_args")
            or kwargs.get("headless")
            or self.settings.nodriver_background_start
            or self.settings.nodriver_disable_focus_stealing
        )

    def _is_browser_connect_error(self, exc: Exception) -> bool:
        return "Failed to connect to browser" in str(exc)

    def _browser_connect_failure_message(self) -> str:
        message = "Failed to connect to browser"
        if not self.start_diagnostics:
            return message
        latest = self.start_diagnostics[-1]
        suffix = (
            "remote_debugging_port={port}, endpoint_open={endpoint_open}, "
            "endpoint_waited_seconds={waited}, window_mode={window_mode}, "
            "minimal_args_mode={minimal}"
        ).format(
            port=latest.get("remote_debugging_port"),
            endpoint_open=latest.get("endpoint_open"),
            waited=latest.get("endpoint_waited_seconds"),
            window_mode=latest.get("window_mode"),
            minimal=latest.get("minimal_args_mode"),
        )
        return f"{message} ({suffix})"

    def _settings_diagnostics(self) -> dict[str, object]:
        return {
            "NODRIVER_START_TIMEOUT_SECONDS": self.settings.nodriver_start_timeout_seconds,
            "NODRIVER_START_RETRIES": self.settings.nodriver_start_retry_attempts,
            "NODRIVER_START_RETRY_DELAY_SECONDS": (
                self.settings.nodriver_start_retry_delay_seconds
            ),
            "NODRIVER_AFTER_TERMINATE_GRACE_SECONDS": (
                self.settings.nodriver_after_terminate_grace_seconds
            ),
            "NODRIVER_WINDOW_MODE": self.settings.nodriver_window_mode,
            "NODRIVER_PROVIDER_WINDOW_MODE": self.settings.nodriver_provider_window_mode,
            "NODRIVER_LOGIN_WINDOW_MODE": self.settings.nodriver_login_window_mode,
            "NODRIVER_BACKGROUND_START": self.settings.nodriver_background_start,
            "NODRIVER_DISABLE_FOCUS_STEALING": self.settings.nodriver_disable_focus_stealing,
        }

    def _debugging_port_seen_in_chrome_command(
        self,
        attempt_diagnostics: dict[str, Any],
        port: int,
    ) -> bool:
        expected = f"--remote-debugging-port={port}"
        commands = attempt_diagnostics.get("applied_chrome_commands")
        return isinstance(commands, list) and any(expected in str(command) for command in commands)

    @contextmanager
    def _force_nodriver_free_port(
        self,
        start_browser: Callable[..., Any],
        port: int,
    ) -> Any:
        module_name = getattr(start_browser, "__module__", "")
        if not module_name.startswith("nodriver"):
            yield
            return
        try:
            from nodriver.core import util as nodriver_util
        except ImportError:
            yield
            return

        original_free_port = nodriver_util.free_port
        nodriver_util.free_port = lambda: port
        try:
            yield
        finally:
            nodriver_util.free_port = original_free_port

    def _select_remote_debugging_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((REMOTE_DEBUGGING_HOST, 0))
            return int(sock.getsockname()[1])

    async def _apply_post_start_window_behavior(self, browser: Any) -> None:
        try:
            report = await asyncio.to_thread(
                apply_macos_window_adjustment,
                self.settings,
                context=self.lifecycle_context,
            )
        except Exception as exc:
            report = {"attempted": True, "ok": False, "error": str(exc)}
            logger.warning("NoDriver post-start window adjustment failed: %s", exc)
        if self.start_diagnostics:
            self.start_diagnostics[-1]["window_adjustment"] = report
        if report.get("attempted") and not report.get("ok"):
            logger.warning("NoDriver window adjustment did not complete: %s", report)

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
            previous_pids = self._recovered_browser_previous_profile_process_pids
            self._recovered_browser_previous_profile_process_pids = None
            if previous_pids is not None:
                self._cleanup_failed_start(previous_pids)
            else:
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
