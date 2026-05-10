from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from enum import StrEnum

from astra_nexus.team.models import AgentProfile, AgentResult
from astra_nexus.team.prompting import AgentPrompt


class TeamErrorKind(StrEnum):
    TRANSIENT_PROVIDER = "transient_provider_error"
    PERMANENT_PROVIDER = "permanent_provider_error"
    ORCHESTRATION_INTERNAL = "orchestration_internal_error"


class TeamProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        agent_id: str | None = None,
        error_code: str = "provider_error",
        error_kind: TeamErrorKind = TeamErrorKind.TRANSIENT_PROVIDER,
        original_error: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.agent_id = agent_id
        self.error_code = error_code
        self.error_kind = error_kind
        self.original_error = original_error

    @property
    def transient(self) -> bool:
        return self.error_kind == TeamErrorKind.TRANSIENT_PROVIDER


class TeamProvider(ABC):
    name: str
    supports_parallel: bool = False

    @abstractmethod
    async def generate(
        self,
        *,
        profile: AgentProfile,
        user_task: str,
        previous_results: Sequence[AgentResult],
        prompt: AgentPrompt | None = None,
    ) -> str:
        raise NotImplementedError
