from __future__ import annotations

import inspect
from collections.abc import Sequence
from typing import Any

from astra_nexus.brain.base import BrainProvider, BrainResponse
from astra_nexus.brain.nodriver_provider import NoDriverProvider
from astra_nexus.config.settings import Settings
from astra_nexus.team.models import AgentProfile, AgentResult
from astra_nexus.team.prompting import AgentPrompt
from astra_nexus.team.provider import TeamProvider


class NoDriverTeamProvider(TeamProvider):
    name = "nodriver_team"

    def __init__(
        self,
        *,
        brain_provider: BrainProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.brain_provider = brain_provider or NoDriverProvider(settings=settings)

    async def generate(
        self,
        *,
        profile: AgentProfile,
        user_task: str,
        previous_results: Sequence[AgentResult],
        prompt: AgentPrompt | None = None,
    ) -> str:
        full_prompt = self.build_full_prompt(
            profile=profile,
            user_task=user_task,
            previous_results=previous_results,
            prompt=prompt,
        )
        response = self.brain_provider.ask(
            agent_id=profile.profile_id,
            prompt=full_prompt,
            context=self._context(
                profile=profile,
                user_task=user_task,
                previous_results=previous_results,
                prompt=prompt,
            ),
        )
        resolved = await response if inspect.isawaitable(response) else response
        return self._content(resolved)

    def build_full_prompt(
        self,
        *,
        profile: AgentProfile,
        user_task: str,
        previous_results: Sequence[AgentResult],
        prompt: AgentPrompt | None,
    ) -> str:
        system_prompt = prompt.system_prompt if prompt is not None else profile.system_instruction
        user_prompt = (
            prompt.user_prompt
            if prompt is not None
            else self._previous_results_text(previous_results)
        )
        return "\n\n".join(
            [
                "## Системная инструкция агента",
                system_prompt,
                "## Задача пользователя",
                user_task,
                "## Предыдущие результаты команды",
                user_prompt,
                "## Инструкция текущего агента",
                (
                    f"Выполни только этап {profile.role.value}. "
                    "Верни результат этого агента без имитации остальных ролей."
                ),
            ]
        )

    async def close(self) -> None:
        client = getattr(self.brain_provider, "client", None)
        session = getattr(client, "session", None)
        if session is None:
            return
        stop_result = session.stop()
        if inspect.isawaitable(stop_result):
            await stop_result

    def _context(
        self,
        *,
        profile: AgentProfile,
        user_task: str,
        previous_results: Sequence[AgentResult],
        prompt: AgentPrompt | None,
    ) -> dict[str, Any]:
        metadata = prompt.metadata if prompt is not None else {}
        return {
            "task_prompt": user_task,
            "run_id": metadata.get("run_id"),
            "task_id": metadata.get("run_id"),
            "agent_role": profile.role.value,
            "agent_name": profile.display_name,
            "previous_results_count": metadata.get(
                "previous_results_count",
                len(previous_results),
            ),
            "workspace_path": metadata.get("workspace_path"),
        }

    def _content(self, response: BrainResponse | Any) -> str:
        content = getattr(response, "content", response)
        return str(content)

    def _previous_results_text(self, previous_results: Sequence[AgentResult]) -> str:
        if not previous_results:
            return "Пока предыдущих результатов нет."
        lines = []
        for result in previous_results:
            lines.append(f"{result.profile.role.value}: {result.content}")
        return "\n\n".join(lines)
