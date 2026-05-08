from __future__ import annotations

from astra_nexus.agents.base import BaseAgent


class FinalizerAgent(BaseAgent):
    agent_id = "finalizer"
    role = "finalizer"
    display_name = "Финализатор"
    description = "Собирает финальный ответ или артефакт для пользователя."
    instruction = "Собери финальный ответ с учётом проверки критика."
