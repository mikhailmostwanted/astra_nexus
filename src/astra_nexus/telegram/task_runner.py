from __future__ import annotations

import asyncio
import logging

from aiogram.types import Message

from astra_nexus.core.orchestrator import TaskExecutionContext, TaskOrchestrator
from astra_nexus.telegram.notifier import TelegramEventNotifier
from astra_nexus.telegram.presenters.task_cards import render_task_accepted

logger = logging.getLogger(__name__)


class TelegramTaskRunner:
    def __init__(self, orchestrator: TaskOrchestrator) -> None:
        self.orchestrator = orchestrator
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start(
        self,
        *,
        message: Message,
        user_id: str,
        title: str,
        prompt: str,
    ) -> TaskExecutionContext:
        context = self.orchestrator.create_task(user_id=user_id, title=title, prompt=prompt)
        await message.answer(render_task_accepted(context, self.orchestrator.registry.ids()))

        loop = asyncio.get_running_loop()
        notifier = TelegramEventNotifier(bot=message.bot, chat_id=message.chat.id, loop=loop)
        task = asyncio.create_task(self._execute(context, notifier))
        self._tasks[context.task_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(context.task_id, None))
        return context

    async def _execute(
        self,
        context: TaskExecutionContext,
        notifier: TelegramEventNotifier,
    ) -> None:
        try:
            await asyncio.to_thread(self.orchestrator.execute_task, context, notifier)
        except Exception:
            logger.exception("Ошибка выполнения Telegram-задачи %s", context.task_id)
            await notifier.bot.send_message(
                chat_id=notifier.chat_id,
                text=f"Astra Nexus\nЗадача завершилась с ошибкой: {context.task_id}",
            )
