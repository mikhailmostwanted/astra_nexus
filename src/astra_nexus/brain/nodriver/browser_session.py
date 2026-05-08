from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from astra_nexus.brain.nodriver.exceptions import (
    NoDriverDependencyError,
    NoDriverPageLoadError,
)
from astra_nexus.config.settings import Settings

logger = logging.getLogger(__name__)


class BrowserSession:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.browser: Any | None = None
        self.tab: Any | None = None

    async def start(self) -> Any:
        if self.browser is not None:
            return self.browser

        try:
            import nodriver as uc
        except ImportError as exc:
            raise NoDriverDependencyError() from exc

        user_data_dir = Path(self.settings.nodriver_user_data_dir)
        user_data_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Запуск NoDriver browser profile: %s", user_data_dir)
        self.browser = await uc.start(
            headless=self.settings.nodriver_headless,
            user_data_dir=str(user_data_dir),
        )
        return self.browser

    async def open_chatgpt(self) -> Any:
        browser = await self.start()
        try:
            self.tab = await asyncio.wait_for(
                browser.get(self.settings.nodriver_chatgpt_url),
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
