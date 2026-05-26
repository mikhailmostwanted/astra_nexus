from __future__ import annotations

import inspect
from collections.abc import Sequence
from typing import Any

from astra_nexus.brain.base import BrainProvider, BrainResponse
from astra_nexus.brain.nodriver.exceptions import NoDriverProviderError
from astra_nexus.brain.nodriver_provider import NoDriverProvider
from astra_nexus.config.settings import Settings
from astra_nexus.team.models import AgentProfile, AgentResult
from astra_nexus.team.prompting import AgentPrompt
from astra_nexus.team.provider import (
    TeamErrorKind,
    TeamProvider,
    TeamProviderError,
    TeamProviderOutput,
)

TRANSIENT_NODRIVER_ERROR_CODES = {
    "response_timeout",
    "browser_connect_failed",
    "prompt_insert_failed",
    "chatgpt_ui_not_ready",
    "requested_file_missing",
    "unavailable",
}

PERMANENT_NODRIVER_ERROR_CODES = {
    "login_required",
    "profile_locked",
    "prompt_box_not_found",
    "selector_not_found",
}


class NoDriverTeamProvider(TeamProvider):
    name = "nodriver_team"
    supports_parallel = False

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
    ) -> str | TeamProviderOutput:
        full_prompt = self.build_full_prompt(
            profile=profile,
            user_task=user_task,
            previous_results=previous_results,
            prompt=prompt,
        )
        try:
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
            return TeamProviderOutput(
                content=self._content(resolved),
                metadata=self._metadata(resolved),
            )
        except NoDriverProviderError as exc:
            raise TeamProviderError(
                str(exc),
                agent_id=profile.profile_id,
                error_code=exc.error_code,
                error_kind=self._error_kind(exc.error_code),
                original_error=exc,
            ) from exc

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
            "step_id": metadata.get("execution_step_id"),
            "agent_task_id": metadata.get("agent_task_id"),
            "attempt_number": metadata.get("attempt_number"),
            "output_requested_as_file": bool(metadata.get("output_requested_as_file")),
            "requested_output_format": metadata.get("requested_output_format"),
            "input_artifacts": metadata.get("input_artifacts", []),
            "intent": metadata.get("intent"),
        }

    def _content(self, response: BrainResponse | Any) -> str:
        content = getattr(response, "content", response)
        return str(content)

    def _metadata(self, response: BrainResponse | Any) -> dict[str, Any]:
        metadata = getattr(response, "metadata", {})
        if not isinstance(metadata, dict):
            return {}
        return {
            "brain_provider": getattr(response, "provider", ""),
            **metadata,
        }

    def _previous_results_text(self, previous_results: Sequence[AgentResult]) -> str:
        if not previous_results:
            return "Пока предыдущих результатов нет."
        lines = []
        for result in previous_results:
            lines.append(f"{result.profile.role.value}: {result.content}")
        return "\n\n".join(lines)

    def _error_kind(self, error_code: str) -> TeamErrorKind:
        if error_code in PERMANENT_NODRIVER_ERROR_CODES:
            return TeamErrorKind.PERMANENT_PROVIDER
        if error_code in TRANSIENT_NODRIVER_ERROR_CODES:
            return TeamErrorKind.TRANSIENT_PROVIDER
        return TeamErrorKind.TRANSIENT_PROVIDER
