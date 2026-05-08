from __future__ import annotations

from astra_nexus.agents.base import BaseAgent


class ResearcherAgent(BaseAgent):
    agent_id = "researcher"
    role = "researcher"
    display_name = "Исследователь"
    description = "Собирает факты, ограничения и исходный материал."
    instruction = "Собери опорные факты и явно отметь допущения."
