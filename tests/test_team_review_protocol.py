from __future__ import annotations

import asyncio
import inspect
import json

from astra_nexus.team import (
    AgentRole,
    AsyncTeamOrchestrator,
    FakeTeamProvider,
    TeamRunWorkspace,
)
from astra_nexus.team import review_preview as review_preview_module


def test_review_protocol_creates_task_brief_and_quality_criteria() -> None:
    outcome = asyncio.run(
        AsyncTeamOrchestrator(provider=FakeTeamProvider()).run("проверь идею AI-команды")
    )

    brief = outcome.run.task_brief
    assert brief is not None
    assert brief.original_user_input == "проверь идею AI-команды"
    assert brief.normalized_goal == "проверь идею AI-команды"
    assert brief.expected_output
    assert brief.created_by == AgentRole.COORDINATOR.value
    assert brief.created_at is not None

    assert [criterion.criterion_id for criterion in outcome.run.quality_criteria]
    assert all(criterion.required for criterion in outcome.run.quality_criteria)
    assert all(
        criterion.source_agent == AgentRole.COORDINATOR.value
        for criterion in outcome.run.quality_criteria
    )


def test_critic_creates_review_notes_and_editor_receives_revision_requests() -> None:
    provider = FakeTeamProvider(
        responses={
            AgentRole.CRITIC: "Не хватает явных критериев успеха и проверки ограничений.",
        }
    )

    outcome = asyncio.run(
        AsyncTeamOrchestrator(provider=provider).run("составь краткий план AI-команды")
    )

    assert len(outcome.run.review_notes) == 1
    note = outcome.run.review_notes[0]
    assert note.author_role == AgentRole.CRITIC.value
    assert note.severity == "major"
    assert "критериев успеха" in note.message

    assert len(outcome.run.revision_requests) == 1
    request = outcome.run.revision_requests[0]
    assert request.requested_by == AgentRole.CRITIC.value
    assert request.target_role == AgentRole.EDITOR.value
    assert list(request.related_notes) == [note.note_id]
    assert request.must_fix_before_final is True

    editor_call = next(call for call in provider.calls if call.profile.role == AgentRole.EDITOR)
    assert "Бриф задачи" in editor_call.prompt.user_prompt
    assert "Критерии качества" in editor_call.prompt.user_prompt
    assert "Замечания критика" in editor_call.prompt.user_prompt
    assert "Запросы на доработку" in editor_call.prompt.user_prompt
    assert "явно учитывает" in editor_call.prompt.system_prompt


def test_qa_controller_can_approve_and_final_package_is_created() -> None:
    outcome = asyncio.run(
        AsyncTeamOrchestrator(provider=FakeTeamProvider()).run("собери финальный ответ")
    )

    decision = outcome.run.review_decision
    assert decision is not None
    assert decision.approved is True
    assert decision.needs_revision is False
    assert list(decision.blocking_notes) == []

    package = outcome.run.final_package
    assert package is not None
    assert package.final_text == outcome.final_text
    assert package.applied_revision_count == 0
    assert package.brief_summary
    assert package.quality_check_summary == decision.summary


def test_qa_controller_can_request_one_revision_loop_and_then_final_composer_runs() -> None:
    provider = FakeTeamProvider(
        responses={
            AgentRole.QA_CONTROLLER: "needs_revision=true\nНужно усилить ограничения.",
        }
    )

    outcome = asyncio.run(
        AsyncTeamOrchestrator(provider=provider, max_revision_loops=1).run("проверь слабые места")
    )

    roles = [call.profile.role for call in provider.calls]
    assert roles.count(AgentRole.EDITOR) == 2
    assert roles.count(AgentRole.QA_CONTROLLER) == 2
    assert roles.count(AgentRole.FINAL_COMPOSER) == 1
    assert outcome.run.revision_loops_count == 1
    assert outcome.run.review_decision is not None
    assert outcome.run.review_decision.needs_revision is True
    assert outcome.run.final_package is not None
    assert outcome.run.final_package.applied_revision_count == 1


def test_revision_loop_does_not_exceed_configured_limit() -> None:
    provider = FakeTeamProvider(
        responses={
            AgentRole.QA_CONTROLLER: "needs_revision=true\nНужно ещё раз доработать.",
        }
    )

    outcome = asyncio.run(
        AsyncTeamOrchestrator(provider=provider, max_revision_loops=1).run("проверь слабые места")
    )

    assert outcome.run.revision_loops_count == 1
    assert [call.profile.role for call in provider.calls].count(AgentRole.EDITOR) == 2
    assert [call.profile.role for call in provider.calls].count(AgentRole.QA_CONTROLLER) == 2


def test_workspace_saves_review_protocol_files(tmp_path) -> None:
    outcome = asyncio.run(
        AsyncTeamOrchestrator(provider=FakeTeamProvider()).run("составь краткий план")
    )

    run_path = TeamRunWorkspace(root_path=tmp_path / "team_runs").save(outcome.run)

    run_payload = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    final_package = json.loads((run_path / "final_package.json").read_text(encoding="utf-8"))
    review_protocol = (run_path / "review_protocol.md").read_text(encoding="utf-8")

    assert (run_path / "task_brief.json").exists()
    assert (run_path / "quality_criteria.json").exists()
    assert (run_path / "review_notes.json").exists()
    assert (run_path / "revision_requests.json").exists()
    assert (run_path / "review_decision.json").exists()
    assert final_package["final_text"] == outcome.final_text
    assert "Task Brief" in review_protocol
    assert "Review Decision" in review_protocol
    assert run_payload["review_protocol_enabled"] is True
    assert run_payload["revision_loops_count"] == 0
    assert run_payload["review_notes_count"] == len(outcome.run.review_notes)
    assert run_payload["final_approved"] is True


def test_prompts_include_review_protocol_blocks_for_later_agents() -> None:
    provider = FakeTeamProvider()

    asyncio.run(AsyncTeamOrchestrator(provider=provider).run("проверь идею AI-команды"))

    final_call = next(
        call for call in provider.calls if call.profile.role == AgentRole.FINAL_COMPOSER
    )
    assert "Бриф задачи" in final_call.prompt.user_prompt
    assert "Критерии качества" in final_call.prompt.user_prompt
    assert "Замечания критика" in final_call.prompt.user_prompt
    assert "Запросы на доработку" in final_call.prompt.user_prompt


def test_review_preview_cli_runs_with_fake_provider_and_file(tmp_path, capsys) -> None:
    file_path = tmp_path / "idea.md"
    file_path.write_text("Идея AI-команды", encoding="utf-8")

    exit_code = review_preview_module.main(
        [
            "--workspace-root",
            str(tmp_path / "team_runs"),
            "--file",
            str(file_path),
            "проверь файл и найди слабые места",
        ]
    )

    output = capsys.readouterr().out
    source = inspect.getsource(review_preview_module)
    assert exit_code == 0
    assert "final result:" in output
    assert "brief:" in output
    assert "review decision:" in output
    assert "revision loops count:" in output
    assert "workspace path:" in output
    assert "NoDriver" not in source
    assert "nodriver" not in source
