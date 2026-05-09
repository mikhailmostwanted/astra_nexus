from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.exceptions import NoDriverProviderError
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


async def amain() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    session = BrowserSession(settings)
    user_data_dir = session.user_data_dir

    print("Astra Nexus NoDriver diagnose")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Chrome: {detect_chrome_path(settings.nodriver_browser_executable_path)}")
    print(f"user_data_dir: {user_data_dir}")
    print(f"profile_exists: {user_data_dir.exists()}")
    print(f"headless: {settings.nodriver_headless}")
    print(f"no_sandbox: {settings.nodriver_no_sandbox}")
    print(f"start_timeout: {settings.nodriver_start_timeout_seconds}")
    print("Проверка запуска браузера: about:blank")

    try:
        await session.open_url("about:blank")
    except NoDriverProviderError as exc:
        print(f"status: {exc.status}")
        print(f"message: {exc}")
        print(f"action: {exc.action}")
        return 1
    finally:
        await session.stop()

    print("status: ok")
    print("message: браузер запущен и about:blank открыт")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
