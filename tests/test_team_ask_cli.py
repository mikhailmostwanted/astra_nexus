from __future__ import annotations

import json

from astra_nexus.team import FakeTeamProvider
from astra_nexus.team import ask as ask_module


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
