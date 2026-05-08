from __future__ import annotations

import logging

from fastapi import FastAPI

from astra_nexus.agents.registry import create_default_registry
from astra_nexus.brain.base import BrainProvider
from astra_nexus.brain.dummy_provider import DummyBrainProvider
from astra_nexus.brain.nodriver_provider import NoDriverProvider
from astra_nexus.config.settings import Settings, load_settings
from astra_nexus.core.orchestrator import TaskOrchestrator
from astra_nexus.db.session import create_session_factory, init_db
from astra_nexus.services.agent_service import AgentService
from astra_nexus.services.message_service import MessageService
from astra_nexus.services.task_service import TaskService
from astra_nexus.utils.logging import configure_logging

from .routes import agents, health, tasks

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)

    session_factory = create_session_factory(settings.database_url)
    init_db(session_factory)

    registry = create_default_registry()
    task_service = TaskService(session_factory)
    agent_service = AgentService(session_factory)
    message_service = MessageService(session_factory)
    agent_service.sync_registry(registry)

    app = FastAPI(title=settings.app_name)
    app.state.settings = settings
    app.state.task_service = task_service
    app.state.agent_service = agent_service
    app.state.message_service = message_service
    app.state.orchestrator = TaskOrchestrator(
        task_service=task_service,
        agent_service=agent_service,
        message_service=message_service,
        brain_provider=_build_brain_provider(settings),
        workspace_base_path=settings.workspace_base_path,
        registry=registry,
    )

    if settings.telegram_bot_token is None:
        logger.info("Telegram bot disabled: TELEGRAM_BOT_TOKEN не задан.")

    app.include_router(health.router)
    app.include_router(tasks.router)
    app.include_router(agents.router)
    return app


def _build_brain_provider(settings: Settings) -> BrainProvider:
    match settings.brain_provider:
        case "dummy":
            return DummyBrainProvider()
        case "nodriver":
            return NoDriverProvider()
        case unknown:
            raise ValueError(f"Неизвестный brain-provider: {unknown}")
