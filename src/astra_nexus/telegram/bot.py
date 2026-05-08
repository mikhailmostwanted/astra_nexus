from __future__ import annotations

import logging

from astra_nexus.config.settings import Settings
from astra_nexus.core.orchestrator import TaskOrchestrator
from astra_nexus.services.agent_service import AgentService
from astra_nexus.services.message_service import MessageService
from astra_nexus.services.task_service import TaskService
from astra_nexus.telegram.task_runner import TelegramTaskRunner

logger = logging.getLogger(__name__)


async def run_bot(
    *,
    settings: Settings,
    orchestrator: TaskOrchestrator,
    task_service: TaskService,
    agent_service: AgentService,
    message_service: MessageService,
) -> None:
    if settings.telegram_bot_token is None:
        logger.info("Telegram bot disabled: TELEGRAM_BOT_TOKEN не задан.")
        return

    from aiogram import Bot, Dispatcher

    from astra_nexus.telegram.handlers.agents import router as agents_router
    from astra_nexus.telegram.handlers.cancel import router as cancel_router
    from astra_nexus.telegram.handlers.start import router as start_router
    from astra_nexus.telegram.handlers.status import router as status_router
    from astra_nexus.telegram.handlers.tasks import router as tasks_router

    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    dispatcher = Dispatcher()
    dispatcher["orchestrator"] = orchestrator
    dispatcher["task_runner"] = TelegramTaskRunner(orchestrator)
    dispatcher["task_service"] = task_service
    dispatcher["agent_service"] = agent_service
    dispatcher["message_service"] = message_service
    dispatcher["settings"] = settings
    dispatcher.include_router(start_router)
    dispatcher.include_router(agents_router)
    dispatcher.include_router(tasks_router)
    dispatcher.include_router(status_router)
    dispatcher.include_router(cancel_router)

    logger.info("Telegram bot polling started.")
    await dispatcher.start_polling(bot)
