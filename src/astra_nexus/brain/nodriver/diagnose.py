from __future__ import annotations

import shutil
import sys
from pathlib import Path

from astra_nexus.brain.nodriver.lifecycle import NoDriverLifecycleManager
from astra_nexus.config.settings import load_settings
from astra_nexus.utils.logging import configure_logging

CHROME_CANDIDATES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "chrome",
]

MACOS_CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


def detect_chrome_path(configured_path: Path | None) -> str:
    if configured_path is not None:
        return str(configured_path.expanduser().resolve())
    for command in CHROME_CANDIDATES:
        found = shutil.which(command)
        if found:
            return found
    for path in MACOS_CHROME_PATHS:
        if Path(path).exists():
            return path
    return "не найден в PATH; NoDriver попробует автообнаружение"


def main() -> None:
    raise SystemExit(run())


def run() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    lifecycle = NoDriverLifecycleManager(settings, context="diagnose")
    snapshot = lifecycle.inspect()
    lock_info = snapshot.lock_info

    print("Astra Nexus NoDriver diagnose")
    print(f"Python: {sys.version.split()[0]}")
    print(f"provider: {settings.brain_provider}")
    print(f"Chrome: {detect_chrome_path(settings.nodriver_browser_executable_path)}")
    print(f"user_data_dir: {snapshot.user_data_dir}")
    print(f"profile_exists: {snapshot.user_data_dir_exists}")
    print(f"lock_file: {snapshot.lock_path}")
    print(f"lock_exists: {lock_info is not None}")
    print(f"lock_pid: {lock_info.pid if lock_info else 'none'}")
    print(f"lock_context: {lock_info.context if lock_info else 'none'}")
    print(f"lock_pid_alive: {snapshot.lock_pid_alive}")
    print(f"profile_locked: {snapshot.profile_locked}")
    print(
        "live_profile_processes: "
        + (
            ", ".join(str(process.pid) for process in snapshot.live_profile_processes)
            if snapshot.live_profile_processes
            else "none"
        )
    )
    print(f"headless: {settings.nodriver_headless}")
    print(f"window_mode: {settings.nodriver_window_mode}")
    print(f"window_size: {settings.nodriver_window_width}x{settings.nodriver_window_height}")
    print(f"window_position: {settings.nodriver_window_x},{settings.nodriver_window_y}")
    print(f"no_sandbox: {settings.nodriver_no_sandbox}")
    print(f"start_timeout: {settings.nodriver_start_timeout_seconds}")
    print(f"chatgpt_url: {settings.nodriver_chatgpt_url}")
    print("browser_opened: false")
    print("message: diagnose не открывает Chrome; для реальной проверки запусти smoke")
    return 1 if snapshot.profile_locked else 0


if __name__ == "__main__":
    main()
