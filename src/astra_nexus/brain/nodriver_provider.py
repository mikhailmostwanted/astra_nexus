from __future__ import annotations

import logging
from typing import Any

from astra_nexus.brain.base import BrainProvider, BrainResponse
from astra_nexus.brain.nodriver.chatgpt_client import ChatGPTClient
from astra_nexus.brain.nodriver.exceptions import NoDriverProviderError
from astra_nexus.config.settings import Settings, load_settings

logger = logging.getLogger(__name__)


class NoDriverProvider(BrainProvider):
    name = "nodriver"

    def __init__(
        self, settings: Settings | None = None, client: ChatGPTClient | None = None
    ) -> None:
        self.settings = settings or load_settings()
        self.client = client or ChatGPTClient(self.settings)

    async def ask(
        self,
        agent_id: str,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> BrainResponse:
        full_prompt = self._build_prompt(agent_id=agent_id, prompt=prompt, context=context or {})
        try:
            content = await self.client.ask(full_prompt)
        except NoDriverProviderError:
            logger.exception("NoDriverProvider недоступен для агента %s", agent_id)
            raise
        return BrainResponse(
            content=content,
            provider=self.name,
            metadata={
                "agent_id": agent_id,
                "mode": self.settings.nodriver_agent_mode,
                "chatgpt_url": self.settings.nodriver_chatgpt_url,
            },
        )

    def _build_prompt(self, agent_id: str, prompt: str, context: dict[str, Any]) -> str:
        previous_messages = context.get("previous_messages", [])
        task_prompt = context.get("task_prompt", "")
        return (
            "Ты агент в системе Astra Nexus.\n"
            f"agent_id: {agent_id}\n"
            f"Задача пользователя: {task_prompt}\n"
            f"Контекстных сообщений: {len(previous_messages)}\n\n"
            "Ответь по своей роли кратко, структурированно и без лишней вводной.\n\n"
            f"{prompt}"
        )
