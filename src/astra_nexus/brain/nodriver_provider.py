from __future__ import annotations

from typing import Any

from astra_nexus.brain.base import BrainProvider, BrainResponse


class NoDriverProvider(BrainProvider):
    name = "nodriver"

    def ask(
        self,
        agent_id: str,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> BrainResponse:
        # TODO: подключить NoDriver + ChatGPT Web после стабилизации MVP-контракта.
        raise NotImplementedError(
            "NoDriverProvider пока является архитектурной заглушкой без браузерной автоматизации."
        )
