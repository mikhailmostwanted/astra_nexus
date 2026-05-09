from __future__ import annotations

import asyncio
import inspect

from astra_nexus.team import (
    DEFAULT_AGENT_PIPELINE,
    AgentRole,
    AgentTaskStatus,
    AsyncTeamOrchestrator,
    FakeTeamProvider,
    RunEventType,
    RunStatus,
    TeamProviderError,
)
from astra_nexus.team import orchestrator as orchestrator_module


def test_team_run_created_and_pipeline_finishes_in_order() -> None:
    provider = FakeTeamProvider()
    orchestrator = AsyncTeamOrchestrator(provider=provider)

    outcome = asyncio.run(orchestrator.run("Подготовить план запуска Astra Nexus"))

    assert outcome.run.id.startswith("team_run_")
    assert outcome.run.user_task == "Подготовить план запуска Astra Nexus"
    assert outcome.run.status == RunStatus.COMPLETED
    assert [task.profile.role for task in outcome.run.tasks] == DEFAULT_AGENT_PIPELINE
    assert [task.status for task in outcome.run.tasks] == [AgentTaskStatus.COMPLETED] * 6
    assert [result.profile.role for result in outcome.run.results] == DEFAULT_AGENT_PIPELINE
    assert [call.profile.role for call in provider.calls] == DEFAULT_AGENT_PIPELINE


def test_team_run_events_are_ready_for_future_telegram_log() -> None:
    provider = FakeTeamProvider()
    orchestrator = AsyncTeamOrchestrator(provider=provider)

    outcome = asyncio.run(orchestrator.run("Проверить документ"))

    event_types = [event.type for event in outcome.run.events]
    assert event_types[0] == RunEventType.RUN_STARTED
    assert event_types[-1] == RunEventType.RUN_FINISHED
    assert event_types.count(RunEventType.AGENT_STARTED) == 6
    assert event_types.count(RunEventType.AGENT_FINISHED) == 6
    assert RunEventType.RUN_FAILED not in event_types
    assert RunEventType.AGENT_FAILED not in event_types

    first_agent_event = next(
        event for event in outcome.run.events if event.type == RunEventType.AGENT_STARTED
    )
    assert first_agent_event.payload["role"] == AgentRole.COORDINATOR.value
    assert first_agent_event.message == "Агент coordinator начал работу."


def test_team_orchestrator_returns_final_composer_text() -> None:
    provider = FakeTeamProvider()
    orchestrator = AsyncTeamOrchestrator(provider=provider)

    outcome = asyncio.run(orchestrator.run("Собрать итоговый ответ"))

    assert outcome.final_text == "fake:final_composer:Собрать итоговый ответ:context=5"
    assert outcome.run.results[-1].content == outcome.final_text
    assert provider.calls[-1].profile.role == AgentRole.FINAL_COMPOSER
    assert provider.calls[-1].previous_results_count == 5


def test_team_run_fails_when_agent_provider_fails() -> None:
    provider = FakeTeamProvider(fail_on=AgentRole.CRITIC)
    orchestrator = AsyncTeamOrchestrator(provider=provider)

    try:
        asyncio.run(orchestrator.run("Найти слабые места"))
    except TeamProviderError:
        pass
    else:
        raise AssertionError("TeamProviderError was not raised")

    run = orchestrator.runs[-1]
    assert run.status == RunStatus.FAILED
    assert [task.profile.role for task in run.tasks] == [
        AgentRole.COORDINATOR,
        AgentRole.ANALYST,
        AgentRole.CRITIC,
    ]
    assert run.tasks[-1].status == AgentTaskStatus.FAILED
    assert [result.profile.role for result in run.results] == [
        AgentRole.COORDINATOR,
        AgentRole.ANALYST,
    ]
    assert [event.type for event in run.events][-2:] == [
        RunEventType.AGENT_FAILED,
        RunEventType.RUN_FAILED,
    ]


def test_team_orchestrator_depends_on_team_provider_not_nodriver_provider() -> None:
    source = inspect.getsource(orchestrator_module)

    assert "NoDriver" not in source
    assert "nodriver" not in source
    assert "TeamProvider" in source
