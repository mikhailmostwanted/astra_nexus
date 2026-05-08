from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from astra_nexus.agents.registry import AgentRegistry, create_default_registry
from astra_nexus.brain.base import BrainProvider
from astra_nexus.brain.factory import build_brain_provider
from astra_nexus.config.settings import Settings, load_settings
from astra_nexus.core.orchestrator import TaskOrchestrator
from astra_nexus.db.session import create_session_factory, init_db
from astra_nexus.services.agent_service import AgentService
from astra_nexus.services.message_service import MessageService
from astra_nexus.services.task_service import TaskService
from astra_nexus.utils.logging import configure_logging


@dataclass(frozen=True)
class AppContainer:
    settings: Settings
    session_factory: sessionmaker[Session]
    registry: AgentRegistry
    task_service: TaskService
    agent_service: AgentService
    message_service: MessageService
    brain_provider: BrainProvider
    orchestrator: TaskOrchestrator


def build_container(settings: Settings | None = None) -> AppContainer:
    settings = settings or load_settings()
    configure_logging(settings.log_level)

    session_factory = create_session_factory(settings.database_url)
    init_db(session_factory)

    registry = create_default_registry()
    task_service = TaskService(session_factory)
    agent_service = AgentService(session_factory)
    message_service = MessageService(session_factory)
    agent_service.sync_registry(registry)

    brain_provider = build_brain_provider(settings)
    orchestrator = TaskOrchestrator(
        task_service=task_service,
        agent_service=agent_service,
        message_service=message_service,
        brain_provider=brain_provider,
        workspace_base_path=settings.workspace_base_path,
        registry=registry,
    )

    return AppContainer(
        settings=settings,
        session_factory=session_factory,
        registry=registry,
        task_service=task_service,
        agent_service=agent_service,
        message_service=message_service,
        brain_provider=brain_provider,
        orchestrator=orchestrator,
    )
