from __future__ import annotations

import logging

from fastapi import FastAPI

from astra_nexus.bootstrap import build_container
from astra_nexus.config.settings import Settings, load_settings

from .routes import agents, brain, health, tasks

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    container = build_container(settings)

    app = FastAPI(title=settings.app_name)
    app.state.settings = settings
    app.state.task_service = container.task_service
    app.state.agent_service = container.agent_service
    app.state.message_service = container.message_service
    app.state.brain_provider = container.brain_provider
    app.state.orchestrator = container.orchestrator

    if settings.telegram_bot_token is None:
        logger.info("Telegram bot disabled: TELEGRAM_BOT_TOKEN не задан.")

    app.include_router(health.router)
    app.include_router(tasks.router)
    app.include_router(agents.router)
    app.include_router(brain.router)
    return app
