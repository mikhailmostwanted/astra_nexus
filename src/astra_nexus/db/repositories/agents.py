from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from astra_nexus.db.models import Agent


class AgentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, agent_id: str, role: str, name: str, description: str) -> Agent:
        agent = self.session.get(Agent, agent_id)
        if agent is None:
            agent = Agent(id=agent_id, role=role, name=name, description=description)
            self.session.add(agent)
            return agent

        agent.role = role
        agent.name = name
        agent.description = description
        agent.is_active = True
        return agent

    def list_active(self) -> list[Agent]:
        stmt = select(Agent).where(Agent.is_active.is_(True)).order_by(Agent.id)
        return list(self.session.scalars(stmt))
