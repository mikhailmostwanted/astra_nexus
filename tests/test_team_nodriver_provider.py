from __future__ import annotations

import asyncio

from astra_nexus.brain.base import BrainResponse
from astra_nexus.team import AgentPrompt, AgentRole
from astra_nexus.team.nodriver_provider import NoDriverTeamProvider
from astra_nexus.team.profiles import default_profiles_by_role


class RecordingBrainProvider:
    name = "recording_nodriver"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def ask(self, agent_id: str, prompt: str, context: dict | None = None) -> BrainResponse:
        self.calls.append(
            {
                "agent_id": agent_id,
                "prompt": prompt,
                "context": context or {},
            }
        )
        return BrainResponse(
            content="Ответ нижележащего NoDriverProvider",
            provider=self.name,
            metadata={"recorded": True},
        )


def test_nodriver_team_provider_builds_full_prompt_for_brain_provider(tmp_path) -> None:
    brain_provider = RecordingBrainProvider()
    provider = NoDriverTeamProvider(brain_provider=brain_provider)
    profile = default_profiles_by_role()[AgentRole.CRITIC]
    prompt = AgentPrompt(
        system_prompt="SYSTEM: Вера ищет слабые места.",
        user_prompt=(
            "USER: задача пользователя.\nПредыдущие результаты команды:\ncoordinator: план команды."
        ),
        metadata={
            "run_id": "team_run_123",
            "workspace_path": str(tmp_path / "team_run_123"),
        },
    )

    result = asyncio.run(
        provider.generate(
            profile=profile,
            user_task="Проверить стратегию",
            previous_results=(),
            prompt=prompt,
        )
    )

    assert result == "Ответ нижележащего NoDriverProvider"
    call = brain_provider.calls[0]
    full_prompt = call["prompt"]
    assert call["agent_id"] == AgentRole.CRITIC.value
    assert full_prompt.index("## Системная инструкция агента") < full_prompt.index(
        "## Задача пользователя"
    )
    assert full_prompt.index("## Задача пользователя") < full_prompt.index(
        "## Предыдущие результаты команды"
    )
    assert full_prompt.index("## Предыдущие результаты команды") < full_prompt.index(
        "## Инструкция текущего агента"
    )
    assert "SYSTEM: Вера ищет слабые места." in full_prompt
    assert "Проверить стратегию" in full_prompt
    assert "coordinator: план команды." in full_prompt
    assert "Выполни только этап critic" in full_prompt


def test_nodriver_team_provider_passes_expected_context_to_brain_provider(tmp_path) -> None:
    brain_provider = RecordingBrainProvider()
    provider = NoDriverTeamProvider(brain_provider=brain_provider)
    profile = default_profiles_by_role()[AgentRole.FINAL_COMPOSER]
    prompt = AgentPrompt(
        system_prompt="SYSTEM",
        user_prompt="USER",
        metadata={
            "run_id": "team_run_999",
            "previous_results_count": 5,
            "workspace_path": str(tmp_path / "team_run_999"),
        },
    )

    asyncio.run(
        provider.generate(
            profile=profile,
            user_task="Собрать финальный ответ",
            previous_results=(),
            prompt=prompt,
        )
    )

    context = brain_provider.calls[0]["context"]
    assert context["task_prompt"] == "Собрать финальный ответ"
    assert context["run_id"] == "team_run_999"
    assert context["agent_role"] == AgentRole.FINAL_COMPOSER.value
    assert context["agent_name"] == "Саша / Финальный сборщик"
    assert context["previous_results_count"] == 5
    assert context["workspace_path"] == str(tmp_path / "team_run_999")


def test_nodriver_team_provider_reports_no_parallel_support() -> None:
    provider = NoDriverTeamProvider(brain_provider=RecordingBrainProvider())

    assert provider.supports_parallel is False
