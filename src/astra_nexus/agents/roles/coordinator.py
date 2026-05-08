from __future__ import annotations

from astra_nexus.agents.base import BaseAgent


class CoordinatorAgent(BaseAgent):
    agent_id = "coordinator"
    role = "coordinator"
    display_name = "Координатор"
    description = "Разбивает задачу на понятный маршрут работы агентов."
    instruction = "Составь краткий план и обозначь порядок работы."
