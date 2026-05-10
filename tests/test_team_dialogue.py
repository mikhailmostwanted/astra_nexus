from __future__ import annotations

import asyncio
import inspect
import json

from astra_nexus.team import (
    AgentRole,
    AsyncTeamOrchestrator,
    FakeTeamProvider,
    TeamAttachmentProcessor,
    TeamDialoguePhase,
    TeamMessageChannel,
    TeamRunWorkspace,
)
from astra_nexus.team import dialogue_preview as dialogue_preview_module


def test_dialogue_turns_are_created_in_pipeline_order() -> None:
    outcome = asyncio.run(
        AsyncTeamOrchestrator(provider=FakeTeamProvider()).run("проверь идею AI-команды")
    )

    visible_agent_turns = [
        turn for turn in outcome.run.dialogue_turns if turn.agent_role is not None
    ]

    assert [turn.agent_role for turn in visible_agent_turns[0::2]] == [
        AgentRole.COORDINATOR,
        AgentRole.ANALYST,
        AgentRole.CRITIC,
        AgentRole.EDITOR,
        AgentRole.QA_CONTROLLER,
        AgentRole.FINAL_COMPOSER,
    ]
    assert visible_agent_turns[0].phase == TeamDialoguePhase.COORDINATION
    assert "Понял задачу" in visible_agent_turns[0].text
    assert visible_agent_turns[-1].text == "Финальный ответ собран."


def test_critic_editor_and_qa_dialogue_phases_have_expected_order() -> None:
    outcome = asyncio.run(
        AsyncTeamOrchestrator(provider=FakeTeamProvider()).run("разбери слабые места")
    )
    turns = outcome.run.dialogue_turns

    critic_index = next(
        index
        for index, turn in enumerate(turns)
        if turn.agent_role == AgentRole.CRITIC and turn.phase == TeamDialoguePhase.CRITIQUE
    )
    editor_index = next(
        index
        for index, turn in enumerate(turns)
        if turn.agent_role == AgentRole.EDITOR and turn.phase == TeamDialoguePhase.REVISION
    )
    qa_index = next(
        index
        for index, turn in enumerate(turns)
        if turn.agent_role == AgentRole.QA_CONTROLLER and turn.phase == TeamDialoguePhase.QA
    )
    final_index = next(
        index
        for index, turn in enumerate(turns)
        if turn.agent_role == AgentRole.FINAL_COMPOSER
        and turn.phase == TeamDialoguePhase.FINALIZATION
    )

    assert critic_index < editor_index < qa_index < final_index
    assert "слабые места" in turns[critic_index].text
    assert turns[editor_index].reply_to_role == AgentRole.CRITIC
    assert "по замечаниям" in turns[editor_index].text
    assert "Проверяю" in turns[qa_index].text


def test_workspace_saves_team_chat_json_and_markdown(tmp_path) -> None:
    outcome = asyncio.run(
        AsyncTeamOrchestrator(provider=FakeTeamProvider()).run("составь краткий план")
    )

    run_path = TeamRunWorkspace(root_path=tmp_path / "team_runs").save(outcome.run)

    run_payload = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    team_chat_payload = json.loads((run_path / "team_chat.json").read_text(encoding="utf-8"))
    team_chat_markdown = (run_path / "team_chat.md").read_text(encoding="utf-8")
    assert run_payload["dialogue_turns_count"] == len(outcome.run.dialogue_turns)
    assert team_chat_payload["run_id"] == outcome.run.id
    assert len(team_chat_payload["turns"]) == len(outcome.run.dialogue_turns)
    assert "[Артём]" in team_chat_markdown
    assert "Понял задачу" in team_chat_markdown
    assert "Финальный ответ собран." in team_chat_markdown


def test_metadata_only_attachment_gets_human_dialogue_reaction(tmp_path) -> None:
    attachment_path = tmp_path / "archive.bin"
    attachment_path.write_bytes(b"\x00\x01\x02")
    attachments = TeamAttachmentProcessor().prepare_paths([attachment_path], source="test")

    outcome = asyncio.run(
        AsyncTeamOrchestrator(provider=FakeTeamProvider()).run(
            "проверь файл",
            attachments=attachments,
        )
    )

    assert any(
        "Файл вижу, но текст из него пока не извлечён" in turn.text
        for turn in outcome.run.dialogue_turns
    )


def test_dialogue_messages_go_to_main_chat_and_events_to_log_chat() -> None:
    outcome = asyncio.run(
        AsyncTeamOrchestrator(
            provider=FakeTeamProvider(),
            pipeline=[AgentRole.COORDINATOR],
        ).run("проверь разделение каналов")
    )

    main_messages = [
        message
        for message in outcome.run.messages
        if message.channel == TeamMessageChannel.MAIN_CHAT
    ]
    log_messages = [
        message
        for message in outcome.run.messages
        if message.channel == TeamMessageChannel.LOG_CHAT
    ]

    assert [message.text for message in main_messages] == [
        "Понял задачу. Сейчас сформулирую цель и рабочий маршрут для команды.",
        "Маршрут есть, передаю на разбор.",
        "Готово, финальная версия собрана.",
    ]
    assert all(
        "Командный run" in message.text or "Координатор" in message.text for message in log_messages
    )


def test_dialogue_preview_cli_runs_with_fake_provider_and_file(tmp_path, capsys) -> None:
    file_path = tmp_path / "note.md"
    file_path.write_text("Контекст файла", encoding="utf-8")

    exit_code = dialogue_preview_module.main(["--file", str(file_path), "проверь файл"])

    output = capsys.readouterr().out
    source = inspect.getsource(dialogue_preview_module)
    assert exit_code == 0
    assert "[Артём]" in output
    assert "[Саша]" in output
    assert "Контекст файла" not in output
    assert "NoDriver" not in source
    assert "nodriver" not in source
