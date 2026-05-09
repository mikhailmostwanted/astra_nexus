from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from astra_nexus.team.models import AgentProfile, AgentResult


class TeamProviderError(RuntimeError):
    def __init__(self, message: str, *, agent_id: str | None = None) -> None:
        super().__init__(message)
        self.agent_id = agent_id


class TeamProvider(ABC):
    name: str

    @abstractmethod
    async def generate(
        self,
        *,
        profile: AgentProfile,
        user_task: str,
        previous_results: Sequence[AgentResult],
    ) -> str:
        raise NotImplementedError
