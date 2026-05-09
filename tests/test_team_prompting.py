from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from astra_nexus.team import (
    AgentContext,
    AgentPrompt,
    AgentResult,
    AgentRole,
    AsyncTeamOrchestrator,
    FakeTeamProvider,
    TeamPromptBuilder,
)
from astra_nexus.team import prompting as prompting_module
from astra_nexus.team.profiles import default_profiles_by_role


def test_prompt_builder_creates_system_and_user_prompt() -> None:
    profile = default_profiles_by_role()[AgentRole.COORDINATOR]
    prompt = TeamPromptBuilder().build(
        profile=profile,
        context=AgentContext(
            run_id="team_run_test",
            user_task="Разобрать стратегию запуска",
            current_agent_role=profile.role,
            current_agent_name=profile.display_name,
            previous_results=(),
        ),
    )

    assert isinstance(prompt, AgentPrompt)
    assert "Артём / Координатор" in prompt.system_prompt
    assert "не пишет финальный ответ" in prompt.system_prompt
    assert "Разобрать стратегию запуска" in prompt.user_prompt
    assert prompt.metadata["agent_role"] == AgentRole.COORDINATOR.value
    assert prompt.metadata["previous_results_count"] == 0


@pytest.mark.parametrize(
    ("role", "instruction_fragment"),
    [
        (AgentRole.COORDINATOR, "выдаёт план для команды"),
        (AgentRole.ANALYST, "разбирает факты"),
        (AgentRole.CRITIC, "не переписывает всё сам"),
        (AgentRole.EDITOR, "сохраняет смысл задачи"),
        (AgentRole.QA_CONTROLLER, "что надо поправить перед финалом"),
        (AgentRole.FINAL_COMPOSER, "не упоминает внутреннюю кухню"),
    ],
)
def test_prompt_builder_uses_role_specific_instructions(
    role: AgentRole,
    instruction_fragment: str,
) -> None:
    profile = default_profiles_by_role()[role]
    prompt = TeamPromptBuilder().build(
        profile=profile,
        context=AgentContext(
            run_id="team_run_test",
            user_task="Проверить план",
            current_agent_role=role,
            current_agent_name=profile.display_name,
            previous_results=(),
        ),
    )

    assert instruction_fragment in prompt.system_prompt
    assert role.value in prompt.metadata["agent_role"]


def test_prompt_builder_adds_previous_results_to_next_agent_prompt() -> None:
    profiles = default_profiles_by_role()
    previous_result = AgentResult(
        run_id="team_run_test",
        task_id="agent_task_coordinator",
        profile=profiles[AgentRole.COORDINATOR],
        content="План координатора: сначала анализ, потом критика.",
    )

    prompt = TeamPromptBuilder().build(
        profile=profiles[AgentRole.CRITIC],
        context=AgentContext(
            run_id="team_run_test",
            user_task="Проверить план",
            current_agent_role=AgentRole.CRITIC,
            current_agent_name=profiles[AgentRole.CRITIC].display_name,
            previous_results=(previous_result,),
        ),
    )

    assert "Предыдущие результаты команды" in prompt.user_prompt
    assert "coordinator" in prompt.user_prompt
    assert "План координатора" in prompt.user_prompt
    assert prompt.metadata["previous_results_count"] == 1


def test_orchestrator_passes_built_prompt_to_provider_and_final_gets_full_context() -> None:
    provider = FakeTeamProvider()
    orchestrator = AsyncTeamOrchestrator(provider=provider)

    asyncio.run(orchestrator.run("Собрать финальный ответ"))

    final_call = provider.calls[-1]
    assert final_call.profile.role == AgentRole.FINAL_COMPOSER
    assert final_call.previous_results_count == 5
    assert final_call.prompt is not None
    assert "fake:coordinator:Собрать финальный ответ:context=0" in final_call.prompt.user_prompt
    assert "fake:qa_controller:Собрать финальный ответ:context=4" in final_call.prompt.user_prompt
    assert "Саша / Финальный сборщик" in final_call.prompt.system_prompt


def test_orchestrator_uses_injected_prompt_builder() -> None:
    class RecordingPromptBuilder(TeamPromptBuilder):
        def __init__(self) -> None:
            super().__init__()
            self.roles: list[AgentRole] = []

        def build(self, *, profile, context):  # noqa: ANN001
            self.roles.append(context.current_agent_role)
            return super().build(profile=profile, context=context)

    prompt_builder = RecordingPromptBuilder()
    orchestrator = AsyncTeamOrchestrator(
        provider=FakeTeamProvider(),
        prompt_builder=prompt_builder,
        workspace_path=Path("data/team_runs/team_run_test"),
    )

    asyncio.run(orchestrator.run("Проверить builder"))

    assert prompt_builder.roles == [
        AgentRole.COORDINATOR,
        AgentRole.ANALYST,
        AgentRole.CRITIC,
        AgentRole.EDITOR,
        AgentRole.QA_CONTROLLER,
        AgentRole.FINAL_COMPOSER,
    ]


def test_team_prompting_does_not_import_nodriver() -> None:
    source = inspect.getsource(prompting_module)

    assert "NoDriver" not in source
    assert "nodriver" not in source
