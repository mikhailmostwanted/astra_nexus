from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Sequence

import pytest

from astra_nexus.team import (
    AgentRole,
    AgentTaskStatus,
    AsyncTeamOrchestrator,
    FakeTeamProvider,
    RunStatus,
    TeamExecutionMode,
    TeamProviderError,
    TeamRunWorkspace,
)
from astra_nexus.team import parallel_preview as parallel_preview_module
from astra_nexus.team.execution_plan import default_parallel_execution_plan
from astra_nexus.team.models import AgentProfile, AgentResult
from astra_nexus.team.prompting import AgentPrompt


class DelayedParallelProvider(FakeTeamProvider):
    def __init__(self, delays: dict[AgentRole, float] | None = None) -> None:
        super().__init__()
        self.delays = delays or {}
        self.started_at: dict[AgentRole, float] = {}
        self.finished_at: dict[AgentRole, float] = {}

    async def generate(
        self,
        *,
        profile: AgentProfile,
        user_task: str,
        previous_results: Sequence[AgentResult],
        prompt: AgentPrompt | None = None,
    ) -> str:
        loop = asyncio.get_running_loop()
        self.started_at[profile.role] = loop.time()
        if delay := self.delays.get(profile.role):
            await asyncio.sleep(delay)
        result = await super().generate(
            profile=profile,
            user_task=user_task,
            previous_results=previous_results,
            prompt=prompt,
        )
        self.finished_at[profile.role] = loop.time()
        return result


class SequentialOnlyProvider(FakeTeamProvider):
    supports_parallel = False


class CancellableParallelProvider(FakeTeamProvider):
    def __init__(self) -> None:
        super().__init__()
        self._started_roles: set[AgentRole] = set()
        self.parallel_started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled_roles: set[AgentRole] = set()

    async def generate(
        self,
        *,
        profile: AgentProfile,
        user_task: str,
        previous_results: Sequence[AgentResult],
        prompt: AgentPrompt | None = None,
    ) -> str:
        if profile.role in {AgentRole.ANALYST, AgentRole.CRITIC}:
            self._started_roles.add(profile.role)
            if self._started_roles == {AgentRole.ANALYST, AgentRole.CRITIC}:
                self.parallel_started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled_roles.add(profile.role)
                raise
        return await super().generate(
            profile=profile,
            user_task=user_task,
            previous_results=previous_results,
            prompt=prompt,
        )


def test_sequential_execution_mode_is_default() -> None:
    outcome = asyncio.run(AsyncTeamOrchestrator(provider=FakeTeamProvider()).run("сделай план"))

    assert outcome.run.execution_mode == TeamExecutionMode.SEQUENTIAL
    assert [result.profile.role for result in outcome.run.results] == [
        AgentRole.COORDINATOR,
        AgentRole.ANALYST,
        AgentRole.CRITIC,
        AgentRole.EDITOR,
        AgentRole.QA_CONTROLLER,
        AgentRole.FINAL_COMPOSER,
    ]


def test_default_parallel_plan_documents_dependencies() -> None:
    plan = default_parallel_execution_plan()

    assert plan.mode == TeamExecutionMode.PARALLEL
    assert [step.roles for step in plan.steps] == [
        (AgentRole.COORDINATOR,),
        (AgentRole.ANALYST, AgentRole.CRITIC),
        (AgentRole.EDITOR,),
        (AgentRole.QA_CONTROLLER,),
        (AgentRole.FINAL_COMPOSER,),
    ]
    assert plan.dependencies_for(AgentRole.ANALYST) == (AgentRole.COORDINATOR,)
    assert plan.dependencies_for(AgentRole.CRITIC) == (AgentRole.COORDINATOR,)
    assert plan.dependencies_for(AgentRole.EDITOR) == (
        AgentRole.COORDINATOR,
        AgentRole.ANALYST,
        AgentRole.CRITIC,
    )


def test_parallel_mode_runs_independent_agents_concurrently() -> None:
    provider = DelayedParallelProvider(delays={AgentRole.ANALYST: 0.05, AgentRole.CRITIC: 0.05})
    orchestrator = AsyncTeamOrchestrator(
        provider=provider,
        execution_mode=TeamExecutionMode.PARALLEL,
    )

    asyncio.run(orchestrator.run("проверь идею"))

    assert provider.started_at[AgentRole.CRITIC] < provider.finished_at[AgentRole.ANALYST]
    assert provider.started_at[AgentRole.ANALYST] < provider.finished_at[AgentRole.CRITIC]
    calls_by_role = {call.profile.role: call for call in provider.calls}
    assert calls_by_role[AgentRole.ANALYST].previous_results_count == 1
    assert calls_by_role[AgentRole.CRITIC].previous_results_count == 1


def test_parallel_results_keep_deterministic_pipeline_order() -> None:
    provider = DelayedParallelProvider(delays={AgentRole.ANALYST: 0.08, AgentRole.CRITIC: 0.01})
    outcome = asyncio.run(
        AsyncTeamOrchestrator(
            provider=provider,
            execution_mode=TeamExecutionMode.PARALLEL,
        ).run("собери финал")
    )

    assert [result.profile.role for result in outcome.run.results] == [
        AgentRole.COORDINATOR,
        AgentRole.ANALYST,
        AgentRole.CRITIC,
        AgentRole.EDITOR,
        AgentRole.QA_CONTROLLER,
        AgentRole.FINAL_COMPOSER,
    ]
    assert outcome.final_text == "fake:final_composer:собери финал:context=5"


def test_provider_without_parallel_support_falls_back_to_sequential() -> None:
    provider = SequentialOnlyProvider()
    outcome = asyncio.run(
        AsyncTeamOrchestrator(
            provider=provider,
            execution_mode=TeamExecutionMode.PARALLEL,
        ).run("сделай план")
    )

    assert outcome.run.execution_mode == TeamExecutionMode.SEQUENTIAL
    assert [call.profile.role for call in provider.calls] == [
        AgentRole.COORDINATOR,
        AgentRole.ANALYST,
        AgentRole.CRITIC,
        AgentRole.EDITOR,
        AgentRole.QA_CONTROLLER,
        AgentRole.FINAL_COMPOSER,
    ]


def test_parallel_agent_error_marks_run_failed() -> None:
    orchestrator = AsyncTeamOrchestrator(
        provider=FakeTeamProvider(fail_on=AgentRole.CRITIC),
        execution_mode=TeamExecutionMode.PARALLEL,
    )

    with pytest.raises(TeamProviderError):
        asyncio.run(orchestrator.run("проверь риски"))

    run = orchestrator.runs[-1]
    assert run.status == RunStatus.FAILED
    assert run.execution_mode == TeamExecutionMode.PARALLEL
    critic_task = next(task for task in run.tasks if task.profile.role == AgentRole.CRITIC)
    assert critic_task.status == AgentTaskStatus.FAILED


def test_parallel_cancellation_cancels_active_agent_tasks() -> None:
    async def scenario() -> None:
        provider = CancellableParallelProvider()
        orchestrator = AsyncTeamOrchestrator(
            provider=provider,
            execution_mode=TeamExecutionMode.PARALLEL,
        )

        task = asyncio.create_task(orchestrator.run("останови выполнение"))
        await asyncio.wait_for(provider.parallel_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        run = orchestrator.runs[-1]
        assert run.status == RunStatus.CANCELLED
        assert provider.cancelled_roles == {AgentRole.ANALYST, AgentRole.CRITIC}
        cancelled_tasks = {
            agent_task.profile.role: agent_task
            for agent_task in run.tasks
            if agent_task.profile.role in {AgentRole.ANALYST, AgentRole.CRITIC}
        }
        assert all(task.status == AgentTaskStatus.FAILED for task in cancelled_tasks.values())
        assert all(task.error_message == "cancelled" for task in cancelled_tasks.values())

    asyncio.run(scenario())


def test_parallel_workspace_saves_execution_plan_and_timeline(tmp_path) -> None:
    outcome = asyncio.run(
        AsyncTeamOrchestrator(
            provider=FakeTeamProvider(),
            execution_mode=TeamExecutionMode.PARALLEL,
        ).run("сохрани план")
    )

    run_path = TeamRunWorkspace(root_path=tmp_path / "team_runs").save(outcome.run)

    run_payload = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    plan_payload = json.loads((run_path / "execution_plan.json").read_text(encoding="utf-8"))
    tasks_payload = json.loads((run_path / "tasks.json").read_text(encoding="utf-8"))
    timeline = (run_path / "execution_timeline.md").read_text(encoding="utf-8")
    assert run_payload["execution_mode"] == TeamExecutionMode.PARALLEL.value
    assert plan_payload["mode"] == TeamExecutionMode.PARALLEL.value
    assert any(step["mode"] == TeamExecutionMode.PARALLEL.value for step in plan_payload["steps"])
    assert next(task for task in tasks_payload if task["role"] == "editor")["dependencies"] == [
        "coordinator",
        "analyst",
        "critic",
    ]
    assert "## Parallel Steps" in timeline
    assert "analyst, critic" in timeline


def test_parallel_dialogue_marks_parallel_agent_starts_before_finishes() -> None:
    outcome = asyncio.run(
        AsyncTeamOrchestrator(
            provider=DelayedParallelProvider(
                delays={AgentRole.ANALYST: 0.03, AgentRole.CRITIC: 0.03}
            ),
            execution_mode=TeamExecutionMode.PARALLEL,
        ).run("проверь диалог")
    )

    turns = outcome.run.dialogue_turns
    analyst_start = next(
        index for index, turn in enumerate(turns) if turn.agent_role == AgentRole.ANALYST
    )
    critic_start = next(
        index for index, turn in enumerate(turns) if turn.agent_role == AgentRole.CRITIC
    )
    analyst_finish = next(
        index
        for index, turn in enumerate(turns)
        if turn.agent_role == AgentRole.ANALYST and "Разбор готов" in turn.text
    )
    critic_finish = next(
        index
        for index, turn in enumerate(turns)
        if turn.agent_role == AgentRole.CRITIC and "Нашла" in turn.text
    )
    assert analyst_start < critic_finish
    assert critic_start < analyst_finish


def test_parallel_preview_cli_uses_fake_provider(tmp_path, capsys) -> None:
    exit_code = parallel_preview_module.main(
        ["--workspace-root", str(tmp_path / "team_runs"), "проверь идею AI-команды"]
    )

    output = capsys.readouterr().out
    source = inspect.getsource(parallel_preview_module)
    assert exit_code == 0
    assert "execution_mode: parallel" in output
    assert "workspace_path:" in output
    assert "agent_started:" in output
    assert "fake:final_composer" in output
    assert "NoDriver" not in source
    assert "nodriver" not in source


def test_execution_plan_for_intents() -> None:
    from astra_nexus.team.execution_plan import execution_plan_for_mode
    from astra_nexus.team.intake import TeamInputIntent

    # Simple Answer
    plan = execution_plan_for_mode(
        TeamExecutionMode.SEQUENTIAL, pipeline=[], intent=TeamInputIntent.SIMPLE_ANSWER
    )
    assert len(plan.steps) == 1
    assert plan.steps[0].roles == (AgentRole.FINAL_COMPOSER,)
    assert plan.metadata.get("strategy") == "simple_answer_intent"

    # File Generation
    plan = execution_plan_for_mode(
        TeamExecutionMode.SEQUENTIAL, pipeline=[], intent=TeamInputIntent.FILE_GENERATION
    )
    assert len(plan.steps) == 2
    assert plan.steps[0].roles == (AgentRole.ANALYST,)
    assert plan.steps[1].roles == (AgentRole.FINAL_COMPOSER,)

    # File Task
    plan = execution_plan_for_mode(
        TeamExecutionMode.SEQUENTIAL, pipeline=[], intent=TeamInputIntent.FILE_TASK
    )
    assert len(plan.steps) == 3
    assert plan.steps[0].roles == (AgentRole.ANALYST,)
    assert plan.steps[1].roles == (AgentRole.EDITOR,)
    assert plan.steps[2].roles == (AgentRole.FINAL_COMPOSER,)

    # Debug Mode
    plan = execution_plan_for_mode(
        TeamExecutionMode.SEQUENTIAL, pipeline=[], intent=TeamInputIntent.DEBUG_MODE
    )
    assert len(plan.steps) == 4
    assert plan.steps[0].roles == (AgentRole.ANALYST,)
    assert plan.steps[3].roles == (AgentRole.FINAL_COMPOSER,)


def test_orchestrator_handles_file_generation_intent() -> None:
    from astra_nexus.team.intake import TeamInputIntent

    provider = FakeTeamProvider()
    orchestrator = AsyncTeamOrchestrator(provider=provider)

    outcome = asyncio.run(
        orchestrator.run("сделай отчет в pdf", intent=TeamInputIntent.FILE_GENERATION)
    )

    assert outcome.run.execution_plan.metadata.get("strategy") == "file_generation_intent"
    roles = [result.profile.role for result in outcome.run.results]
    assert roles == [AgentRole.ANALYST, AgentRole.FINAL_COMPOSER]
