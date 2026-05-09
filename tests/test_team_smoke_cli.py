from __future__ import annotations

import inspect
import json

import astra_nexus.team as team_package
from astra_nexus.team import smoke as smoke_module


def test_team_smoke_cli_runs_fake_provider_and_writes_workspace(tmp_path, capsys) -> None:
    exit_code = smoke_module.main(
        [
            "Собери smoke-report",
            "--workspace-root",
            str(tmp_path / "team_runs"),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "status: completed" in output
    assert "run_id: team_run_" in output
    assert "final_result:" in output
    assert "workspace_path:" in output

    workspace_line = next(
        line for line in output.splitlines() if line.startswith("workspace_path:")
    )
    workspace_path = tmp_path / "team_runs" / workspace_line.rsplit("/", maxsplit=1)[-1]
    run_payload = json.loads((workspace_path / "run.json").read_text(encoding="utf-8"))
    assert run_payload["user_task"] == "Собери smoke-report"
    assert run_payload["status"] == "completed"


def test_team_smoke_cli_uses_default_task_when_argument_missing(tmp_path, capsys) -> None:
    exit_code = smoke_module.main(["--workspace-root", str(tmp_path / "team_runs")])

    output = capsys.readouterr().out
    workspace_line = next(
        line for line in output.splitlines() if line.startswith("workspace_path:")
    )
    workspace_path = tmp_path / "team_runs" / workspace_line.rsplit("/", maxsplit=1)[-1]
    run_payload = json.loads((workspace_path / "run.json").read_text(encoding="utf-8"))

    assert exit_code == 0
    assert run_payload["user_task"] == "Составь краткий план улучшения Astra Nexus."


def test_team_workspace_and_smoke_cli_do_not_import_nodriver() -> None:
    source = inspect.getsource(smoke_module)
    package_source = inspect.getsource(team_package)

    assert "NoDriver" not in source
    assert "nodriver" not in source
    assert "NoDriver" not in package_source
    assert "nodriver" not in package_source
    assert "FakeTeamProvider" in source
