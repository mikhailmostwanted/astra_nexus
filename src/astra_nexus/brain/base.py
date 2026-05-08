from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BrainResponse:
    content: str
    provider: str
    metadata: dict[str, Any] = field(default_factory=dict)


class BrainProvider(ABC):
    name: str

    @abstractmethod
    def ask(
        self,
        agent_id: str,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> BrainResponse | Awaitable[BrainResponse]:
        raise NotImplementedError


class BrainProviderError(RuntimeError):
    status = "unavailable"
    user_message = "brain-provider недоступен"
    action = "проверь настройки brain-provider"

    def __init__(self, message: str | None = None, *, action: str | None = None) -> None:
        super().__init__(message or self.user_message)
        if action is not None:
            self.action = action
