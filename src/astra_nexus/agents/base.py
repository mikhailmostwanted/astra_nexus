from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from astra_nexus.brain.base import BrainProvider


@dataclass(frozen=True)
class AgentOutput:
    agent_id: str
    role: str
    content: str
    metadata: dict[str, Any]


class BaseAgent:
    agent_id: str
    role: str
    display_name: str
    description: str
    instruction: str

    def build_prompt(self, task_prompt: str, context: dict[str, Any]) -> str:
        previous = context.get("previous_messages", [])
        return (
            f"Роль: {self.display_name}\n"
            f"Инструкция: {self.instruction}\n"
            f"Задача пользователя: {task_prompt}\n"
            f"Сообщений в контексте: {len(previous)}"
        )

    def run(
        self,
        brain_provider: BrainProvider,
        task_prompt: str,
        context: dict[str, Any] | None = None,
    ) -> AgentOutput:
        context = context or {}
        prompt = self.build_prompt(task_prompt=task_prompt, context=context)
        response = brain_provider.ask(
            agent_id=self.agent_id,
            prompt=prompt,
            context={**context, "task_prompt": task_prompt},
        )
        return AgentOutput(
            agent_id=self.agent_id,
            role=self.role,
            content=response.content,
            metadata={"provider": response.provider, **response.metadata},
        )
