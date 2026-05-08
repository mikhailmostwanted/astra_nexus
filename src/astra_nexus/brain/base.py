from __future__ import annotations

from abc import ABC, abstractmethod
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
    ) -> BrainResponse:
        raise NotImplementedError
