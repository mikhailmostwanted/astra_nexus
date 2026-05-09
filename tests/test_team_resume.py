from __future__ import annotations

import asyncio
import json

import pytest

from astra_nexus.team import (
    AgentRole,
    AsyncTeamOrchestrator,
    FakeTeamProvider,
    RunStatus,
    TeamProviderError,
    TeamRetryPolicy,
    TeamRunWorkspace,
)
from astra_nexus.team import resume as resume_module
from astra_nexus.team.provider import TeamErrorKind


class RoleFailingProvider(FakeTeamProvider):
    def __init__(self, fail_role: AgentRole | None = None) -> None:
        super().__init__()
        self.fail_role = fail_role
        self.called_roles: list[AgentRole] = []

    async def generate(self, *, profile, user_task, previous_results, prompt=None):  # noqa: ANN001
        self.called_roles.append(profile.role)
        if profile.role == self.fail_role:
            raise TeamProviderError(
                "provider failed",
                agent_id=profile.profile_id,
                error_code="response_timeout",
                error_kind=TeamErrorKind.TRANSIENT_PROVIDER,
            )
        return await super().generate(
            profile=profile,
            user_task=user_task,
            previous_results=previous_results,
            prompt=prompt,
        )


def _create_failed_workspace(tmp_path):
    workspace = TeamRunWorkspace(root_path=tmp_path / "team_runs")
    provider = RoleFailingProvider(fail_role=AgentRole.CRITIC)
    orchestrator = AsyncTeamOrchestrator(
        provider=provider,
        retry_policy=TeamRetryPolicy(max_retries=0, retry_delay_seconds=0),
    )
    with pytest.raises(TeamProviderError):
        asyncio.run(orchestrator.run("Проверь resume"))
    workspace.save(orchestrator.runs[-1])
    return workspace, orchestrator.runs[-1]


def test_failed_run_can_resume_from_failed_agent(tmp_path) -> None:
    workspace, failed_run = _create_failed_workspace(tmp_path)
    loaded_run = workspace.load(failed_run.id)
    resume_provider = RoleFailingProvider()
    orchestrator = AsyncTeamOrchestrator(provider=resume_provider)

    outcome = asyncio.run(orchestrator.resume(loaded_run))

    assert outcome.run.status == RunStatus.COMPLETED
    assert outcome.run.error_message is None
    assert resume_provider.called_roles == [
        AgentRole.CRITIC,
        AgentRole.EDITOR,
        AgentRole.QA_CONTROLLER,
        AgentRole.FINAL_COMPOSER,
    ]
    assert [result.profile.role for result in outcome.run.results] == [
        AgentRole.COORDINATOR,
        AgentRole.ANALYST,
        AgentRole.CRITIC,
        AgentRole.EDITOR,
        AgentRole.QA_CONTROLLER,
        AgentRole.FINAL_COMPOSER,
    ]


def test_team_resume_cli_updates_workspace_and_skips_completed_agents(tmp_path, capsys) -> None:
    _, failed_run = _create_failed_workspace(tmp_path)
    resume_provider = RoleFailingProvider()

    exit_code = resume_module.main(
        [
            failed_run.id,
            "--workspace-root",
            str(tmp_path / "team_runs"),
        ],
        provider=resume_provider,
    )

    output = capsys.readouterr().out
    payload = json.loads(
        (tmp_path / "team_runs" / failed_run.id / "run.json").read_text(encoding="utf-8")
    )
    assert exit_code == 0
    assert "status: completed" in output
    assert "final_result:" in output
    assert payload["status"] == "completed"
    assert payload["error_message"] is None
    assert resume_provider.called_roles[0] == AgentRole.CRITIC
    assert AgentRole.COORDINATOR not in resume_provider.called_roles
    assert AgentRole.ANALYST not in resume_provider.called_roles
