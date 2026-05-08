from __future__ import annotations

from astra_nexus.agents.base import BaseAgent


class CriticAgent(BaseAgent):
    agent_id = "critic"
    role = "critic"
    display_name = "Критик"
    description = "Проверяет результат на риски, пробелы и несоответствия."
    instruction = "Коротко проверь черновик и назови только значимые риски."
