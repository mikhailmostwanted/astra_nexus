from __future__ import annotations

import json

from astra_nexus.team import FakeTeamProvider
from astra_nexus.team import ask as ask_module
from astra_nexus.team.provider import TeamErrorKind, TeamProviderError


def test_team_ask_cli_can_run_with_injected_provider_without_browser(tmp_path, capsys) -> None:
    exit_code = ask_module.main(
        [
            "Ответь кратко: что такое Astra Nexus?",
            "--workspace-root",
            str(tmp_path / "team_runs"),
        ],
        provider=FakeTeamProvider(),
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "status: completed" in output
    assert "run_id: team_run_" in output
    assert "workspace_path:" in output
    assert "final_result:" in output

    workspace_line = next(
        line for line in output.splitlines() if line.startswith("workspace_path:")
    )
    workspace_path = tmp_path / "team_runs" / workspace_line.rsplit("/", maxsplit=1)[-1]
    run_payload = json.loads((workspace_path / "run.json").read_text(encoding="utf-8"))
    assert run_payload["status"] == "completed"
    assert run_payload["user_task"] == "Ответь кратко: что такое Astra Nexus?"


def test_team_ask_cli_prints_resume_hint_for_failed_run(tmp_path, capsys) -> None:
    provider = FakeTeamProvider(fail_on="critic")

    exit_code = ask_module.main(
        [
            "Спровоцировать ошибку",
            "--workspace-root",
            str(tmp_path / "team_runs"),
            "--max-retries",
            "0",
        ],
        provider=provider,
    )

    output = capsys.readouterr().out
    run_id_line = next(line for line in output.splitlines() if line.startswith("run_id:"))
    run_id = run_id_line.split(":", maxsplit=1)[1].strip()
    assert exit_code == 1
    assert "status: failed" in output
    assert f"Можно продолжить: astra-nexus-team-resume {run_id}" in output


def test_team_provider_error_exposes_classification() -> None:
    error = TeamProviderError(
        "temporary",
        error_code="response_timeout",
        error_kind=TeamErrorKind.TRANSIENT_PROVIDER,
    )

    assert error.transient is True
    assert error.error_code == "response_timeout"
    assert error.error_kind == TeamErrorKind.TRANSIENT_PROVIDER
