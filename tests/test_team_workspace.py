from __future__ import annotations

import asyncio
import json

from astra_nexus.team import (
    AgentRole,
    AsyncTeamOrchestrator,
    FakeTeamProvider,
    RunEventType,
    RunStatus,
    TeamRunWorkspace,
)


def test_team_workspace_saves_run_report_files(tmp_path) -> None:
    orchestrator = AsyncTeamOrchestrator(provider=FakeTeamProvider())
    outcome = asyncio.run(orchestrator.run("Составь план проверки workspace."))
    workspace = TeamRunWorkspace(root_path=tmp_path / "team_runs")

    run_path = workspace.save(outcome.run)

    assert run_path == tmp_path / "team_runs" / outcome.run.id
    assert run_path.is_dir()

    run_payload = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert run_payload["run_id"] == outcome.run.id
    assert run_payload["status"] == RunStatus.COMPLETED.value
    assert run_payload["user_task"] == "Составь план проверки workspace."
    assert run_payload["final_result"] == outcome.final_text
    assert run_payload["created_at"]
    assert run_payload["started_at"]
    assert run_payload["finished_at"]
    assert [agent["role"] for agent in run_payload["agents"]] == [role.value for role in AgentRole]

    event_lines = (run_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(event_lines) == len(outcome.run.events)
    first_event = json.loads(event_lines[0])
    assert first_event["event_type"] == RunEventType.RUN_STARTED.value
    assert first_event["run_id"] == outcome.run.id
    assert first_event["timestamp"]
    assert first_event["message"] == "Командный run начат."

    assert (run_path / "final.md").read_text(encoding="utf-8") == outcome.final_text

    for role in AgentRole:
        result_path = run_path / "agent_results" / f"{role.value}.md"
        assert result_path.exists()
        content = result_path.read_text(encoding="utf-8")
        assert f"# {role.value}" in content
        assert "## Задача" in content
        assert "## Статус" in content
        assert "## Результат" in content
