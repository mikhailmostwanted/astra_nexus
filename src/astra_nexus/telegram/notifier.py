from __future__ import annotations

import asyncio
import logging
from concurrent.futures import TimeoutError

from aiogram import Bot

from astra_nexus.core.events import TaskEvent
from astra_nexus.telegram.presenters.agent_messages import render_agent_message
from astra_nexus.telegram.presenters.task_cards import render_task_event

logger = logging.getLogger(__name__)


class TelegramEventNotifier:
    def __init__(self, *, bot: Bot, chat_id: int, loop: asyncio.AbstractEventLoop) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.loop = loop

    def __call__(self, event: TaskEvent) -> None:
        text = self._render(event)
        if text is None:
            return

        future = asyncio.run_coroutine_threadsafe(
            self.bot.send_message(chat_id=self.chat_id, text=text),
            self.loop,
        )
        try:
            future.result(timeout=20)
        except TimeoutError:
            logger.warning("Telegram send timeout for event %s", event.type)
        except Exception:
            logger.exception("Не удалось отправить Telegram event %s", event.type)

    def _render(self, event: TaskEvent) -> str | None:
        if event.type == "agent.message":
            return render_agent_message(event)
        return render_task_event(event)
