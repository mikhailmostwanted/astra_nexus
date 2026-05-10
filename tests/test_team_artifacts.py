from __future__ import annotations

import asyncio
import inspect
import json
import sys
from collections.abc import Sequence

from astra_nexus.team import (
    AgentProfile,
    AgentResult,
    AsyncTeamOrchestrator,
    FakeTeamProvider,
    TeamAttachmentProcessor,
    TeamProvider,
    TeamProviderError,
    TeamRunWorkspace,
)
from astra_nexus.team import artifacts_preview as artifacts_preview_module
from astra_nexus.team.artifacts import TeamArtifactFormat, TeamArtifactType
from astra_nexus.team.prompting import AgentPrompt


def test_output_artifacts_are_created_for_completed_run(tmp_path) -> None:
    file_path = tmp_path / "brief.md"
    file_path.write_text("Контекст исходного файла", encoding="utf-8")
    attachments = TeamAttachmentProcessor().prepare_paths([file_path], source="test")
    outcome = asyncio.run(
        AsyncTeamOrchestrator(provider=FakeTeamProvider()).run(
            "проверь файл и собери итог",
            attachments=attachments,
        )
    )
    workspace = TeamRunWorkspace(root_path=tmp_path / "team_runs")

    run_path = workspace.save(outcome.run)

    artifacts_dir = run_path / "artifacts"
    expected_files = {
        "final_answer.md",
        "executive_summary.md",
        "critic_report.md",
        "qa_report.md",
        "review_protocol.md",
        "source_files_summary.md",
        "run_manifest.json",
        "index.md",
    }
    assert expected_files.issubset({path.name for path in artifacts_dir.iterdir()})
    assert outcome.final_text in (artifacts_dir / "final_answer.md").read_text(encoding="utf-8")

    index_text = (artifacts_dir / "index.md").read_text(encoding="utf-8")
    assert outcome.run.id in index_text
    assert "final_answer.md" in index_text
    assert "executive_summary.md" in index_text

    source_summary = (artifacts_dir / "source_files_summary.md").read_text(encoding="utf-8")
    assert "brief.md" in source_summary
    assert "markdown" in source_summary
    assert "extracted_chars" in source_summary
    assert "prompt_chars" in source_summary

    run_payload = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert run_payload["artifacts_count"] >= len(expected_files)
    assert run_payload["artifacts_dir"] == str(artifacts_dir)
    assert run_payload["primary_artifact_path"] == str(artifacts_dir / "final_answer.md")
    assert run_payload["artifacts_index_path"] == str(artifacts_dir / "index.md")

    manifest = json.loads((artifacts_dir / "run_manifest.json").read_text(encoding="utf-8"))
    artifact_types = {artifact["artifact_type"] for artifact in manifest["artifacts"]}
    assert TeamArtifactType.FINAL_ANSWER.value in artifact_types
    assert TeamArtifactType.REVIEW_PROTOCOL.value in artifact_types
    assert TeamArtifactFormat.MARKDOWN.value in {
        artifact["format"] for artifact in manifest["artifacts"]
    }


def test_failed_run_does_not_create_false_completed_final_artifact(tmp_path) -> None:
    orchestrator = AsyncTeamOrchestrator(provider=FailingArtifactProvider())
    try:
        asyncio.run(orchestrator.run("задача упадёт"))
    except TeamProviderError:
        failed_run = orchestrator.runs[-1]
    else:  # pragma: no cover - explicit guard for test intent
        raise AssertionError("provider should fail")

    run_path = TeamRunWorkspace(root_path=tmp_path / "team_runs").save(failed_run)

    assert failed_run.status.value == "failed"
    assert not (run_path / "artifacts" / "final_answer.md").exists()
    run_payload = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    assert run_payload["artifacts_count"] == 0


def test_requested_output_artifact_is_separate_from_internal_final_answer(tmp_path) -> None:
    outcome = asyncio.run(
        AsyncTeamOrchestrator(provider=FakeTeamProvider()).run(
            "сделай краткий план и пришли файлом"
        )
    )
    outcome.run.runtime_metadata.update(
        {
            "output_requested_as_file": True,
            "requested_output_format": "txt",
        }
    )
    workspace = TeamRunWorkspace(root_path=tmp_path / "team_runs")

    run_path = workspace.save(outcome.run)

    artifacts_dir = run_path / "artifacts"
    assert (artifacts_dir / "requested_output.txt").exists()
    assert (artifacts_dir / "final_answer.md").exists()
    run_payload = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    requested = [
        artifact
        for artifact in run_payload["artifacts"]
        if artifact["artifact_type"] == "requested_output"
    ]
    assert requested[0]["path"].endswith("requested_output.txt")


def test_artifact_generation_does_not_import_nodriver(tmp_path) -> None:
    sys.modules.pop("astra_nexus.team.nodriver_provider", None)
    outcome = asyncio.run(AsyncTeamOrchestrator(provider=FakeTeamProvider()).run("собери итог"))

    TeamRunWorkspace(root_path=tmp_path / "team_runs").save(outcome.run)

    assert "astra_nexus.team.nodriver_provider" not in sys.modules


def test_artifacts_preview_cli_creates_workspace_without_nodriver(tmp_path, capsys) -> None:
    sys.modules.pop("astra_nexus.team.nodriver_provider", None)
    file_path = tmp_path / "idea.md"
    file_path.write_text("Идея для проверки", encoding="utf-8")

    exit_code = artifacts_preview_module.main(
        [
            "--workspace-root",
            str(tmp_path / "team_runs"),
            "--file",
            str(file_path),
            "проверь идею",
        ]
    )

    output = capsys.readouterr().out
    source = inspect.getsource(artifacts_preview_module)
    assert exit_code == 0
    assert "run_id:" in output
    assert "status: completed" in output
    assert "artifacts_dir:" in output
    assert "final_answer.md" in output
    assert "NoDriver" not in source
    assert "nodriver" not in source
    assert "astra_nexus.team.nodriver_provider" not in sys.modules


class FailingArtifactProvider(TeamProvider):
    name = "failing_artifact_test"

    async def generate(
        self,
        *,
        profile: AgentProfile,
        user_task: str,
        previous_results: Sequence[AgentResult],
        prompt: AgentPrompt | None = None,
    ) -> str:
        raise TeamProviderError("controlled artifact failure", agent_id=profile.profile_id)
