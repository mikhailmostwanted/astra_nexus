from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from astra_nexus.agents.registry import AgentRegistry
from astra_nexus.db.models import Agent
from astra_nexus.db.repositories.agents import AgentRepository


class AgentService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def sync_registry(self, registry: AgentRegistry) -> list[Agent]:
        with self.session_factory() as session:
            repository = AgentRepository(session)
            agents = [
                repository.upsert(
                    agent_id=agent.agent_id,
                    role=agent.role,
                    name=agent.display_name,
                    description=agent.description,
                )
                for agent in registry.all()
            ]
            session.commit()
            return agents

    def list_agents(self) -> list[Agent]:
        with self.session_factory() as session:
            return AgentRepository(session).list_active()
