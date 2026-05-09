from __future__ import annotations

import asyncio
import inspect
import json
from collections import defaultdict

import pytest

from astra_nexus.team import (
    AgentRole,
    AsyncTeamOrchestrator,
    FakeTeamProvider,
    InMemoryTeamMessageSink,
    TeamMessageChannel,
    TeamMessageType,
    TeamProviderError,
    TeamRetryPolicy,
    TeamRunWorkspace,
)
from astra_nexus.team import chat_preview as chat_preview_module
from astra_nexus.team.provider import TeamErrorKind


class FlakyMessageProvider(FakeTeamProvider):
    def __init__(self, *, fail_role: AgentRole, failures_before_success: int) -> None:
        super().__init__()
        self.fail_role = fail_role
        self.failures_before_success = failures_before_success
        self.role_attempts: dict[AgentRole, int] = defaultdict(int)

    async def generate(self, *, profile, user_task, previous_results, prompt=None):  # noqa: ANN001
        self.role_attempts[profile.role] += 1
        if (
            profile.role == self.fail_role
            and self.role_attempts[profile.role] <= self.failures_before_success
        ):
            raise TeamProviderError(
                "temporary provider timeout",
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


def test_orchestrator_emits_main_chat_messages_for_run_and_agents() -> None:
    sink = InMemoryTeamMessageSink()
    orchestrator = AsyncTeamOrchestrator(
        provider=FakeTeamProvider(),
        pipeline=[AgentRole.COORDINATOR],
        message_sink=sink,
    )

    outcome = asyncio.run(orchestrator.run("Проверить поток сообщений"))

    main_messages = [
        message for message in sink.messages if message.channel == TeamMessageChannel.MAIN_CHAT
    ]
    assert [message.type for message in main_messages] == [
        TeamMessageType.RUN_STARTED,
        TeamMessageType.AGENT_STARTED,
        TeamMessageType.AGENT_FINISHED,
        TeamMessageType.RUN_FINISHED,
    ]
    assert main_messages[1].author_role == AgentRole.COORDINATOR
    assert main_messages[1].author_name == "Артём"
    assert "Босс, принял задачу" in main_messages[1].text
    assert main_messages[-1].text == "Готово, финальная версия собрана."
    assert outcome.run.messages == sink.messages


def test_retry_emits_main_chat_and_technical_log_messages() -> None:
    sink = InMemoryTeamMessageSink()
    provider = FlakyMessageProvider(
        fail_role=AgentRole.COORDINATOR,
        failures_before_success=1,
    )
    orchestrator = AsyncTeamOrchestrator(
        provider=provider,
        pipeline=[AgentRole.COORDINATOR],
        retry_policy=TeamRetryPolicy(max_retries=1, retry_delay_seconds=0),
        message_sink=sink,
    )

    asyncio.run(orchestrator.run("Проверить retry stream"))

    retry_main = [
        message
        for message in sink.messages
        if message.type == TeamMessageType.AGENT_RETRY
        and message.channel == TeamMessageChannel.MAIN_CHAT
    ]
    retry_log = [
        message
        for message in sink.messages
        if message.type == TeamMessageType.AGENT_RETRY
        and message.channel == TeamMessageChannel.LOG_CHAT
    ]
    assert retry_main[0].text == "Поймал временный сбой, пробую ещё раз."
    assert retry_log[0].metadata["error_code"] == "response_timeout"
    assert retry_log[0].metadata["retry_number"] == 1


def test_failed_run_emits_failed_message_and_resume_hint() -> None:
    sink = InMemoryTeamMessageSink()
    orchestrator = AsyncTeamOrchestrator(
        provider=FakeTeamProvider(fail_on=AgentRole.CRITIC),
        pipeline=[AgentRole.COORDINATOR, AgentRole.CRITIC],
        retry_policy=TeamRetryPolicy(max_retries=0),
        message_sink=sink,
    )

    with pytest.raises(TeamProviderError):
        asyncio.run(orchestrator.run("Проверить failure stream"))

    failed_main = [
        message
        for message in sink.messages
        if message.type == TeamMessageType.AGENT_FAILED
        and message.channel == TeamMessageChannel.MAIN_CHAT
    ]
    failed_log = [
        message
        for message in sink.messages
        if message.type == TeamMessageType.AGENT_FAILED
        and message.channel == TeamMessageChannel.LOG_CHAT
    ]
    assert failed_main[0].text == (
        "На этом шаге упёрся в ошибку. Run сохранён, его можно продолжить."
    )
    assert "astra-nexus-team-resume" in failed_log[0].text
    assert failed_log[0].metadata["error_code"] == "provider_error"


def test_workspace_saves_messages_as_json_and_markdown(tmp_path) -> None:
    orchestrator = AsyncTeamOrchestrator(
        provider=FakeTeamProvider(),
        pipeline=[AgentRole.COORDINATOR],
    )
    outcome = asyncio.run(orchestrator.run("Сохранить сообщения"))

    run_path = TeamRunWorkspace(root_path=tmp_path / "team_runs").save(outcome.run)

    messages_payload = json.loads((run_path / "messages.json").read_text(encoding="utf-8"))
    messages_markdown = (run_path / "messages.md").read_text(encoding="utf-8")
    assert [message["channel"] for message in messages_payload] == [
        TeamMessageChannel.MAIN_CHAT.value,
        TeamMessageChannel.LOG_CHAT.value,
        TeamMessageChannel.MAIN_CHAT.value,
        TeamMessageChannel.LOG_CHAT.value,
        TeamMessageChannel.MAIN_CHAT.value,
        TeamMessageChannel.LOG_CHAT.value,
        TeamMessageChannel.MAIN_CHAT.value,
        TeamMessageChannel.LOG_CHAT.value,
    ]
    assert "## Main Chat" in messages_markdown
    assert "[Артём]" in messages_markdown
    assert "Босс, принял задачу" in messages_markdown
    assert "## Log Chat" in messages_markdown
    assert "[Лог]" in messages_markdown


def test_resume_appends_messages_instead_of_overwriting(tmp_path) -> None:
    workspace = TeamRunWorkspace(root_path=tmp_path / "team_runs")
    failing_orchestrator = AsyncTeamOrchestrator(
        provider=FakeTeamProvider(fail_on=AgentRole.CRITIC),
        pipeline=[AgentRole.COORDINATOR, AgentRole.CRITIC],
        retry_policy=TeamRetryPolicy(max_retries=0),
    )
    with pytest.raises(TeamProviderError):
        asyncio.run(failing_orchestrator.run("Проверить append resume"))
    failed_run = failing_orchestrator.runs[-1]
    workspace.save(failed_run)
    messages_before = list(failed_run.messages)

    loaded_run = workspace.load(failed_run.id)
    resume_sink = InMemoryTeamMessageSink(seed=loaded_run.messages)
    resume_orchestrator = AsyncTeamOrchestrator(
        provider=FakeTeamProvider(),
        pipeline=[AgentRole.COORDINATOR, AgentRole.CRITIC],
        message_sink=resume_sink,
    )
    outcome = asyncio.run(resume_orchestrator.resume(loaded_run))
    workspace.save(outcome.run)

    reloaded_run = workspace.load(failed_run.id)
    assert len(reloaded_run.messages) > len(messages_before)
    assert [message.id for message in reloaded_run.messages[: len(messages_before)]] == [
        message.id for message in messages_before
    ]
    assert reloaded_run.messages[-1].type == TeamMessageType.RUN_FINISHED


def test_chat_preview_cli_runs_fake_provider_without_nodriver(capsys) -> None:
    exit_code = chat_preview_module.main(["Проверь идею AI-команды для Astra Nexus."])

    output = capsys.readouterr().out
    source = inspect.getsource(chat_preview_module)
    assert exit_code == 0
    assert "[Артём]" in output
    assert "[Вера]" in output
    assert "[Лог]" in output
    assert "Готово, финальная версия собрана." in output
    assert "NoDriver" not in source
    assert "nodriver" not in source
