from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.diagnose import detect_chrome_path
from astra_nexus.brain.nodriver.exceptions import NoDriverProviderError
from astra_nexus.brain.nodriver.lifecycle import NoDriverLifecycleManager
from astra_nexus.config.settings import Settings, load_settings
from astra_nexus.utils.logging import configure_logging

SessionFactory = Callable[[Settings, str], Any]


async def arun(
    *,
    settings: Settings | None = None,
    session_factory: SessionFactory | None = None,
) -> int:
    settings = settings or load_settings()
    lifecycle = NoDriverLifecycleManager(settings, context="doctor")
    snapshot = lifecycle.inspect()
    lock_info = snapshot.lock_info

    print("Astra Nexus NoDriver doctor")
    print(f"Chrome: {detect_chrome_path(settings.nodriver_browser_executable_path)}")
    print(f"user_data_dir: {snapshot.user_data_dir}")
    print(f"profile_exists: {_bool_text(snapshot.user_data_dir_exists)}")
    print(f"lock_file: {snapshot.lock_path}")
    print(f"lock_exists: {_bool_text(lock_info is not None)}")
    print(f"lock_pid: {lock_info.pid if lock_info else 'none'}")
    print(f"lock_context: {lock_info.context if lock_info else 'none'}")
    print(f"lock_pid_alive: {_bool_text(snapshot.lock_pid_alive)}")
    print(f"profile_locked: {_bool_text(snapshot.profile_locked)}")
    print(f"start_timeout: {settings.nodriver_start_timeout_seconds}")
    print(f"start_retries: {settings.nodriver_start_retry_attempts}")
    print(f"start_retry_delay: {settings.nodriver_start_retry_delay_seconds}")
    print(f"after_terminate_grace: {settings.nodriver_after_terminate_grace_seconds}")
    print(f"window_mode: {settings.nodriver_window_mode}")
    print(f"provider_window_mode: {settings.nodriver_provider_window_mode}")
    print(f"login_window_mode: {settings.nodriver_login_window_mode}")
    print(f"background_start: {_bool_text(settings.nodriver_background_start)}")
    print(f"disable_focus_stealing: {_bool_text(settings.nodriver_disable_focus_stealing)}")
    print(f"preferred_model: {settings.nodriver_preferred_model_name or 'none'}")
    print(f"preferred_reasoning_mode: {settings.nodriver_preferred_reasoning_mode or 'none'}")
    print(f"require_preferred_model: {_bool_text(settings.nodriver_require_preferred_model)}")
    print(f"live_profile_processes: {_format_processes(snapshot.live_profile_processes)}")

    if snapshot.profile_locked:
        print("status: profile_locked")
        print(
            "action: заверши указанный Chrome/NoDriver процесс "
            "или выполни astra-nexus-nodriver-clean"
        )
        return 1

    factory = session_factory or _default_session_factory
    session = factory(settings, "doctor")
    try:
        await session.start()
        _print_latest_start_diagnostics(getattr(session, "start_diagnostics", []))
    except NoDriverProviderError as exc:
        print(f"status: {exc.status}")
        print(f"message: {exc}")
        _print_latest_start_diagnostics(exc.details.get("attempts", []))
        print(f"action: {exc.action}")
        return 1
    except Exception as exc:
        print("status: failed")
        print(f"message: {exc}")
        _print_latest_start_diagnostics(getattr(session, "start_diagnostics", []))
        print("action: проверь Chrome, профиль и запусти astra-nexus-nodriver-clean")
        return 1
    finally:
        await session.stop()

    print("status: ok")
    print("message: Chrome и remote debugging endpoint доступны без полного ask")
    return 0


def run() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    return asyncio.run(arun(settings=settings))


def main() -> None:
    raise SystemExit(run())


def _default_session_factory(settings: Settings, lifecycle_context: str) -> BrowserSession:
    return BrowserSession(settings, lifecycle_context=lifecycle_context)


def _print_latest_start_diagnostics(attempts: object) -> None:
    if not isinstance(attempts, list) or not attempts:
        return
    latest = attempts[-1]
    if not isinstance(latest, dict):
        return
    print(f"remote_debugging_host: {latest.get('remote_debugging_host')}")
    print(f"remote_debugging_port: {latest.get('remote_debugging_port')}")
    print(f"diagnostic_window_mode: {latest.get('window_mode')}")
    print(f"minimal_args_mode: {_bool_text(bool(latest.get('minimal_args_mode')))}")
    print(f"headless: {_bool_text(bool(latest.get('headless')))}")
    print(f"chrome_process_started: {_bool_text(bool(latest.get('chrome_process_started')))}")
    print(f"endpoint_open: {_bool_text(bool(latest.get('endpoint_open')))}")
    print(f"endpoint_waited_seconds: {latest.get('endpoint_waited_seconds')}")
    print(f"chrome_args: {latest.get('chrome_args') or 'none'}")
    commands = latest.get("applied_chrome_commands")
    if isinstance(commands, list) and commands:
        print(f"chrome_command: {commands[0]}")


def _format_processes(processes: object) -> str:
    if not isinstance(processes, list) or not processes:
        return "none"
    return ", ".join(str(getattr(process, "pid", process)) for process in processes)


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


if __name__ == "__main__":
    main()
