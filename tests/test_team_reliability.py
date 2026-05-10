from __future__ import annotations

import asyncio
from collections import defaultdict

import pytest

from astra_nexus.team import (
    AgentRole,
    AsyncTeamOrchestrator,
    FakeTeamProvider,
    RunEventType,
    RunStatus,
    TeamPromptBuilder,
    TeamProviderError,
    TeamRetryPolicy,
    TeamRunWorkspace,
)
from astra_nexus.team.provider import TeamErrorKind


class FlakyTeamProvider(FakeTeamProvider):
    def __init__(
        self,
        *,
        fail_role: AgentRole,
        failures_before_success: int,
        error_code: str = "response_timeout",
    ) -> None:
        super().__init__()
        self.fail_role = fail_role
        self.failures_before_success = failures_before_success
        self.error_code = error_code
        self.role_attempts: dict[AgentRole, int] = defaultdict(int)

    async def generate(self, *, profile, user_task, previous_results, prompt=None):  # noqa: ANN001
        self.role_attempts[profile.role] += 1
        if (
            profile.role == self.fail_role
            and self.role_attempts[profile.role] <= self.failures_before_success
        ):
            raise TeamProviderError(
                "temporary provider timeout",
                agent_id=profile.profile_id,
                error_code=self.error_code,
                error_kind=TeamErrorKind.TRANSIENT_PROVIDER,
            )
        return await super().generate(
            profile=profile,
            user_task=user_task,
            previous_results=previous_results,
            prompt=prompt,
        )


def test_transient_agent_error_retries_and_succeeds() -> None:
    provider = FlakyTeamProvider(
        fail_role=AgentRole.COORDINATOR,
        failures_before_success=1,
    )
    orchestrator = AsyncTeamOrchestrator(
        provider=provider,
        pipeline=[AgentRole.COORDINATOR],
        retry_policy=TeamRetryPolicy(max_retries=1, retry_delay_seconds=0),
    )

    outcome = asyncio.run(orchestrator.run("Проверь retry"))

    event_types = [event.type for event in outcome.run.events]
    assert outcome.run.status == RunStatus.COMPLETED
    assert provider.role_attempts[AgentRole.COORDINATOR] == 2
    assert RunEventType.AGENT_RETRY_SCHEDULED in event_types
    assert RunEventType.AGENT_RETRY_STARTED in event_types


def test_max_retries_exceeded_fails_run() -> None:
    provider = FlakyTeamProvider(
        fail_role=AgentRole.CRITIC,
        failures_before_success=2,
    )
    orchestrator = AsyncTeamOrchestrator(
        provider=provider,
        pipeline=[AgentRole.COORDINATOR, AgentRole.CRITIC],
        retry_policy=TeamRetryPolicy(max_retries=1, retry_delay_seconds=0),
    )

    with pytest.raises(TeamProviderError):
        asyncio.run(orchestrator.run("Проверь max retries"))

    run = orchestrator.runs[-1]
    event_types = [event.type for event in run.events]
    assert run.status == RunStatus.FAILED
    assert provider.role_attempts[AgentRole.CRITIC] == 2
    assert event_types.count(RunEventType.AGENT_RETRY_SCHEDULED) == 1
    assert event_types.count(RunEventType.AGENT_RETRY_STARTED) == 1
    assert event_types[-2:] == [RunEventType.AGENT_FAILED, RunEventType.RUN_FAILED]


def test_generic_provider_exception_is_retried_once() -> None:
    class RuntimeFlakyProvider(FakeTeamProvider):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        async def generate(self, *, profile, user_task, previous_results, prompt=None):  # noqa: ANN001
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("temporary provider failure")
            return await super().generate(
                profile=profile,
                user_task=user_task,
                previous_results=previous_results,
                prompt=prompt,
            )

    provider = RuntimeFlakyProvider()
    orchestrator = AsyncTeamOrchestrator(
        provider=provider,
        pipeline=[AgentRole.COORDINATOR],
        retry_policy=TeamRetryPolicy(max_retries=1, retry_delay_seconds=0),
    )

    outcome = asyncio.run(orchestrator.run("Проверь generic retry"))

    assert outcome.run.status == RunStatus.COMPLETED
    assert provider.attempts == 2


def test_previous_results_are_limited_in_prompt_but_full_result_is_saved(tmp_path) -> None:
    long_result = "LONG_RESULT_" + ("x" * 300)
    provider = FakeTeamProvider(responses={AgentRole.COORDINATOR: long_result})
    orchestrator = AsyncTeamOrchestrator(
        provider=provider,
        pipeline=[AgentRole.COORDINATOR, AgentRole.ANALYST],
        prompt_builder=TeamPromptBuilder(previous_results_max_chars=80),
    )

    outcome = asyncio.run(orchestrator.run("Проверь лимит контекста"))
    analyst_prompt = provider.calls[-1].prompt
    run_path = TeamRunWorkspace(root_path=tmp_path / "team_runs").save(outcome.run)

    assert analyst_prompt is not None
    assert "Контекст предыдущих результатов сокращён" in analyst_prompt.user_prompt
    assert ("x" * 200) not in analyst_prompt.user_prompt
    assert outcome.run.results[0].content == long_result
    assert long_result in (run_path / "agent_results" / "coordinator.md").read_text(
        encoding="utf-8"
    )
