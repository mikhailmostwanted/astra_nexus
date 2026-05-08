from __future__ import annotations

from astra_nexus.agents.base import BaseAgent


class WriterAgent(BaseAgent):
    agent_id = "writer"
    role = "writer"
    display_name = "Автор"
    description = "Готовит рабочий черновик результата."
    instruction = "Собери связный черновик на базе плана и исследования."
