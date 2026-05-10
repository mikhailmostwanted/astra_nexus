from __future__ import annotations

import asyncio
import inspect
import json

from astra_nexus.team import (
    FakeTeamProvider,
    TeamConversationController,
    TeamInput,
    TeamInputIntent,
    TeamIntakeRouter,
    TeamRunWorkspace,
)
from astra_nexus.team import intake_preview as intake_preview_module


def test_short_casual_phrase_is_casual_chat_and_does_not_create_run() -> None:
    router = TeamIntakeRouter()
    decision = router.route(TeamInput(text="брат че думаешь"))
    controller = TeamConversationController(
        router=router,
        provider=FakeTeamProvider(),
    )

    result = asyncio.run(controller.handle(TeamInput(text="брат че думаешь")))

    assert decision.intent == TeamInputIntent.CASUAL_CHAT
    assert decision.should_start_run is False
    assert "обычный диалог" in decision.user_visible_reply
    assert result.outcome is None
    assert controller.runs == []


def test_explicit_task_is_new_task_and_controller_creates_run(tmp_path) -> None:
    workspace = TeamRunWorkspace(root_path=tmp_path / "team_runs")
    controller = TeamConversationController(
        provider=FakeTeamProvider(),
        workspace=workspace,
    )

    result = asyncio.run(controller.handle(TeamInput(text="сделай подробный план AI-команды")))

    assert result.decision.intent == TeamInputIntent.NEW_TASK
    assert result.decision.should_start_run is True
    assert result.outcome is not None
    assert result.outcome.run.user_task == "сделай подробный план AI-команды"
    assert result.workspace_path is not None
    run_payload = json.loads((result.workspace_path / "run.json").read_text(encoding="utf-8"))
    assert run_payload["status"] == "completed"


def test_empty_input_without_file_is_empty_input() -> None:
    decision = TeamIntakeRouter().route(TeamInput(text="   "))

    assert decision.intent == TeamInputIntent.EMPTY_INPUT
    assert decision.should_start_run is False
    assert decision.user_visible_reply == "Не вижу задачи. Напиши, что нужно сделать."


def test_file_without_text_is_file_task_and_starts_run() -> None:
    decision = TeamIntakeRouter().route(TeamInput(text="", attachments_count=1))

    assert decision.intent == TeamInputIntent.FILE_TASK
    assert decision.should_start_run is True
    assert decision.user_visible_reply == "Вижу файл. Запускаю команду."


def test_stopall_is_stop_all_intent() -> None:
    decision = TeamIntakeRouter().route(TeamInput(text="/stopall"))

    assert decision.intent == TeamInputIntent.STOP_ALL
    assert decision.should_stop_runs is True
    assert decision.user_visible_reply == "Останавливаю активные процессы команды."


def test_status_text_is_status_request() -> None:
    decision = TeamIntakeRouter().route(TeamInput(text="что сейчас происходит?"))

    assert decision.intent == TeamInputIntent.STATUS_REQUEST
    assert decision.should_start_run is False
    assert decision.user_visible_reply == "Сейчас проверю статус активных задач."


def test_resume_text_with_failed_run_id_is_resume_run() -> None:
    decision = TeamIntakeRouter().route(
        TeamInput(text="продолжи прошлое", failed_run_id="team_run_failed")
    )

    assert decision.intent == TeamInputIntent.RESUME_RUN
    assert decision.should_resume_run is True
    assert decision.target_run_id == "team_run_failed"


def test_followup_with_active_run_is_task_followup() -> None:
    decision = TeamIntakeRouter().route(
        TeamInput(text="учти ещё тональность бренда", active_run_id="team_run_active")
    )

    assert decision.intent == TeamInputIntent.TASK_FOLLOWUP
    assert decision.should_start_run is False
    assert decision.target_run_id == "team_run_active"


def test_revision_with_last_run_is_revise_previous_result() -> None:
    decision = TeamIntakeRouter().route(
        TeamInput(text="сократи и сделай формальнее", last_run_id="team_run_last")
    )

    assert decision.intent == TeamInputIntent.REVISE_PREVIOUS_RESULT
    assert decision.should_start_run is False
    assert decision.target_run_id == "team_run_last"


def test_intake_preview_cli_does_not_import_or_use_nodriver(capsys) -> None:
    exit_code = intake_preview_module.main(["сделай подробный план AI-команды"])

    output = capsys.readouterr().out
    source = inspect.getsource(intake_preview_module)
    assert exit_code == 0
    assert "intent: new_task" in output
    assert "should_start_run: true" in output
    assert "NoDriver" not in source
    assert "nodriver" not in source
