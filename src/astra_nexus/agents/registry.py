from __future__ import annotations

from astra_nexus.agents.base import BaseAgent
from astra_nexus.agents.roles.coordinator import CoordinatorAgent
from astra_nexus.agents.roles.critic import CriticAgent
from astra_nexus.agents.roles.finalizer import FinalizerAgent
from astra_nexus.agents.roles.researcher import ResearcherAgent
from astra_nexus.agents.roles.writer import WriterAgent


class AgentRegistry:
    def __init__(self, agents: list[BaseAgent]) -> None:
        self._agents = {agent.agent_id: agent for agent in agents}

    def all(self) -> list[BaseAgent]:
        return list(self._agents.values())

    def ids(self) -> list[str]:
        return list(self._agents.keys())

    def get(self, agent_id: str) -> BaseAgent:
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise KeyError(f"Агент не зарегистрирован: {agent_id}") from exc


def create_default_registry() -> AgentRegistry:
    return AgentRegistry(
        [
            CoordinatorAgent(),
            ResearcherAgent(),
            WriterAgent(),
            CriticAgent(),
            FinalizerAgent(),
        ]
    )
