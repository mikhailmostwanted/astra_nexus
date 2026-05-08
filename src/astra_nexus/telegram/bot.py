from __future__ import annotations

import logging

from astra_nexus.config.settings import Settings
from astra_nexus.core.orchestrator import TaskOrchestrator
from astra_nexus.services.agent_service import AgentService
from astra_nexus.services.task_service import TaskService

logger = logging.getLogger(__name__)


async def run_bot(
    *,
    settings: Settings,
    orchestrator: TaskOrchestrator,
    task_service: TaskService,
    agent_service: AgentService,
) -> None:
    if settings.telegram_bot_token is None:
        logger.info("Telegram bot disabled: TELEGRAM_BOT_TOKEN не задан.")
        return

    from aiogram import Bot, Dispatcher

    from astra_nexus.telegram.handlers.start import router as start_router
    from astra_nexus.telegram.handlers.status import router as status_router
    from astra_nexus.telegram.handlers.tasks import router as tasks_router

    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    dispatcher = Dispatcher()
    dispatcher["orchestrator"] = orchestrator
    dispatcher["task_service"] = task_service
    dispatcher["agent_service"] = agent_service
    dispatcher.include_router(start_router)
    dispatcher.include_router(tasks_router)
    dispatcher.include_router(status_router)

    await dispatcher.start_polling(bot)
