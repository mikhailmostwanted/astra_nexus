from __future__ import annotations

import asyncio
import inspect

from astra_nexus.team import (
    FakeTeamProvider,
    TeamActiveRun,
    TeamConversationController,
    TeamInput,
    TeamInputIntent,
    TeamRuntimeState,
    TeamRuntimeStatus,
    TeamRunWorkspace,
)
from astra_nexus.team import runtime_preview as runtime_preview_module


def test_runtime_casual_chat_does_not_create_run() -> None:
    controller = TeamConversationController(provider=FakeTeamProvider())

    response = asyncio.run(controller.handle("брат че думаешь"))

    assert response.decision.intent == TeamInputIntent.CASUAL_CHAT
    assert response.status == TeamRuntimeStatus.IDLE
    assert response.run_id is None
    assert response.final_text is None
    assert response.user_visible_reply == "Понял, это обычный диалог, команду не запускаю."
    assert controller.state.active_runs == {}


def test_runtime_new_task_creates_completed_run_and_workspace(tmp_path) -> None:
    controller = TeamConversationController(
        provider=FakeTeamProvider(),
        workspace=TeamRunWorkspace(root_path=tmp_path / "team_runs"),
    )

    response = asyncio.run(controller.handle("сделай краткий план AI-команды"))

    assert response.status == TeamRuntimeStatus.COMPLETED
    assert response.decision.intent == TeamInputIntent.NEW_TASK
    assert response.run_id is not None
    assert response.final_text == "fake:final_composer:сделай краткий план AI-команды:context=5"
    assert response.workspace_path is not None
    assert controller.state.active_runs == {}
    assert controller.state.last_run_id == response.run_id
    assert controller.state.last_completed_run_id == response.run_id


def test_runtime_status_request_reports_last_completed_run(tmp_path) -> None:
    controller = TeamConversationController(
        provider=FakeTeamProvider(),
        workspace=TeamRunWorkspace(root_path=tmp_path / "team_runs"),
    )
    first = asyncio.run(controller.handle("сделай краткий план AI-команды"))

    response = asyncio.run(controller.handle("статус"))

    assert response.status == TeamRuntimeStatus.IDLE
    assert response.run_id == first.run_id
    assert first.run_id in response.user_visible_reply
    assert "Последний завершённый run" in response.user_visible_reply


def test_runtime_empty_input_does_not_create_run() -> None:
    controller = TeamConversationController(provider=FakeTeamProvider())

    response = asyncio.run(controller.handle(TeamInput(text=" ")))

    assert response.decision.intent == TeamInputIntent.EMPTY_INPUT
    assert response.status == TeamRuntimeStatus.IDLE
    assert controller.state.last_run_id is None


def test_runtime_stop_all_clears_active_runs() -> None:
    state = TeamRuntimeState()
    state.active_runs["team_run_active"] = TeamActiveRun(run_id="team_run_active")
    controller = TeamConversationController(provider=FakeTeamProvider(), state=state)

    response = asyncio.run(controller.handle("стоп все"))

    assert response.status == TeamRuntimeStatus.CANCELLED
    assert state.active_runs == {}
    assert state.stopped_runs["team_run_active"].stop_requested is True
    assert state.stopped_runs["team_run_active"].stop_reason == "explicit stop command"
    assert "Остановил активные runs: team_run_active" in response.user_visible_reply


def test_runtime_failed_provider_marks_last_failed_run_and_saves_workspace(tmp_path) -> None:
    controller = TeamConversationController(
        provider=FakeTeamProvider(fail_on="critic"),
        workspace=TeamRunWorkspace(root_path=tmp_path / "team_runs"),
    )

    response = asyncio.run(controller.handle("проверь слабые места идеи"))

    assert response.status == TeamRuntimeStatus.FAILED
    assert response.run_id is not None
    assert controller.state.last_failed_run_id == response.run_id
    assert controller.state.active_runs == {}
    assert response.workspace_path is not None
    assert (response.workspace_path / "run.json").exists()


def test_runtime_task_followup_with_active_run_becomes_contextual_task(tmp_path) -> None:
    controller = TeamConversationController(
        provider=FakeTeamProvider(),
        workspace=TeamRunWorkspace(root_path=tmp_path / "team_runs"),
    )

    response = asyncio.run(
        controller.handle(
            TeamInput(text="учти ещё тональность бренда", active_run_id="team_run_active")
        )
    )

    assert response.decision.intent == TeamInputIntent.TASK_FOLLOWUP
    assert response.status == TeamRuntimeStatus.COMPLETED
    assert response.run_id is not None
    assert "Уточнение к run team_run_active" in response.outcome.run.user_task


def test_runtime_revision_with_last_run_becomes_contextual_task(tmp_path) -> None:
    controller = TeamConversationController(
        provider=FakeTeamProvider(),
        workspace=TeamRunWorkspace(root_path=tmp_path / "team_runs"),
    )

    response = asyncio.run(
        controller.handle(
            TeamInput(text="сократи и сделай формальнее", last_run_id="team_run_last")
        )
    )

    assert response.decision.intent == TeamInputIntent.REVISE_PREVIOUS_RESULT
    assert response.status == TeamRuntimeStatus.COMPLETED
    assert response.run_id is not None
    assert "Правка результата run team_run_last" in response.outcome.run.user_task


def test_runtime_preview_cli_does_not_import_or_use_nodriver(capsys) -> None:
    exit_code = runtime_preview_module.main(["сделай краткий план AI-команды"])

    output = capsys.readouterr().out
    source = inspect.getsource(runtime_preview_module)
    assert exit_code == 0
    assert "intent: new_task" in output
    assert "status: completed" in output
    assert "run_id: team_run_" in output
    assert "NoDriver" not in source
    assert "nodriver" not in source
