from __future__ import annotations

import asyncio
import inspect
import sys
from collections.abc import Sequence

from pydantic import SecretStr

from astra_nexus.config.settings import Settings
from astra_nexus.team import (
    AgentRole,
    TeamMessage,
    TeamMessageChannel,
    TeamMessageType,
    TeamRuntimeStatus,
)
from astra_nexus.team import telegram_bridge as telegram_bridge_module
from astra_nexus.team.attachments import TeamAttachmentProcessor
from astra_nexus.team.fake_provider import FakeTeamProvider
from astra_nexus.team.jobs import TeamJobStatus
from astra_nexus.team.models import AgentProfile, AgentResult
from astra_nexus.team.prompting import AgentPrompt
from astra_nexus.team.provider import TeamProvider, TeamProviderError
from astra_nexus.team.telegram_bridge import (
    RecordingTelegramBot,
    TelegramTeamBridge,
    TelegramTeamBridgeConfig,
    TelegramTeamMessageSink,
    main_bot,
    main_job_preview,
    main_live_preview,
    main_preview,
)


class PausingTeamProvider(FakeTeamProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self._paused = False

    async def generate(
        self,
        *,
        profile: AgentProfile,
        user_task: str,
        previous_results: Sequence[AgentResult],
        prompt: AgentPrompt | None = None,
    ) -> str:
        if profile.role == AgentRole.COORDINATOR and not self._paused:
            self._paused = True
            self.started.set()
            await self.release.wait()
        return await super().generate(
            profile=profile,
            user_task=user_task,
            previous_results=previous_results,
            prompt=prompt,
        )


class FailingTeamProvider(TeamProvider):
    name = "failing_test"

    async def generate(
        self,
        *,
        profile: AgentProfile,
        user_task: str,
        previous_results: Sequence[AgentResult],
        prompt: AgentPrompt | None = None,
    ) -> str:
        raise TeamProviderError("controlled failure", agent_id=profile.profile_id)


class TracebackLikeFailingProvider(TeamProvider):
    name = "traceback_like_failing_test"

    async def generate(
        self,
        *,
        profile: AgentProfile,
        user_task: str,
        previous_results: Sequence[AgentResult],
        prompt: AgentPrompt | None = None,
    ) -> str:
        raise TeamProviderError(
            "Traceback (most recent call last):\nFile secret.py\ncontrolled low-level failure",
            agent_id=profile.profile_id,
        )


class _TelegramMethod:
    def __init__(self, chat_id: int) -> None:
        self.chat_id = chat_id


class MigratingSendMessageBot:
    def __init__(self, *, old_chat_id: int, new_chat_id: int) -> None:
        self.old_chat_id = old_chat_id
        self.new_chat_id = new_chat_id
        self.message_calls: list[dict[str, object]] = []
        self._migration_sent = False

    async def send_message(self, chat_id: int, text: str, **kwargs: object) -> None:
        self.message_calls.append({"chat_id": chat_id, "text": text, **kwargs})
        if chat_id == self.old_chat_id and not self._migration_sent:
            self._migration_sent = True
            from aiogram.exceptions import TelegramMigrateToChat

            raise TelegramMigrateToChat(
                method=_TelegramMethod(chat_id),
                message="group migrated to supergroup",
                migrate_to_chat_id=self.new_chat_id,
            )


def test_telegram_preview_casual_does_not_create_run() -> None:
    bot = RecordingTelegramBot()
    bridge = TelegramTeamBridge(bot=bot, config=TelegramTeamBridgeConfig(provider="fake"))

    response = asyncio.run(bridge.handle_text(chat_id=100, text="брат че думаешь"))

    assert response.decision.intent.value == "casual_chat"
    assert response.status == TeamRuntimeStatus.IDLE
    assert bridge.registry.get(100).state.last_run_id is None
    assert bot.messages[-1].text == (
        "Босс, я на связи. Можем спокойно обсудить или сразу превратить мысль в задачу."
    )


def test_telegram_help_does_not_create_run() -> None:
    async def scenario() -> None:
        provider = FakeTeamProvider()
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(provider="fake"),
            provider_factory=lambda: provider,
        )

        response = await bridge.handle_text(chat_id=100, text="/help")

        assert response.decision.intent.value == "help_request"
        assert response.status == TeamRuntimeStatus.IDLE
        assert provider.calls == []
        assert bridge.jobs.snapshot("100") is None
        assert "/status" in bot.messages[-1].text
        assert "/health" in bot.messages[-1].text

    asyncio.run(scenario())


def test_telegram_health_does_not_create_run(tmp_path) -> None:
    async def scenario() -> None:
        provider = FakeTeamProvider()
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(
                provider="fake",
                workspace_root=tmp_path / "team_runs",
                log_chat_id=200,
            ),
            provider_factory=lambda: provider,
        )

        response = await bridge.handle_text(chat_id=100, text="/health")

        assert response.decision.intent.value == "health_request"
        assert response.status == TeamRuntimeStatus.IDLE
        assert provider.calls == []
        assert bridge.jobs.snapshot("100") is None
        text = bot.messages[-1].text
        assert "provider: fake" in text
        assert "active_job: no" in text
        assert "log_chat: yes" in text
        assert str(tmp_path / "team_runs") in text

    asyncio.run(scenario())


def test_telegram_preview_task_starts_background_job() -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        provider = PausingTeamProvider()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(provider="fake"),
            provider_factory=lambda: provider,
        )

        response = await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        await asyncio.wait_for(provider.started.wait(), timeout=1)

        assert response.status == TeamRuntimeStatus.RUNNING
        assert response.run_id is not None
        assert bridge.jobs.snapshot("100").status == TeamJobStatus.RUNNING
        assert any(
            message.text == "Босс, вижу задачу. Сначала разложу её на части."
            for message in bot.messages
        )

        provider.release.set()
        completed = await asyncio.wait_for(bridge.jobs.wait("100"), timeout=1)
        await bridge.drain_watchers()

        assert completed.status == TeamJobStatus.COMPLETED
        assert bridge.registry.get(100).state.last_completed_run_id == completed.run_id
        assert any("сделай краткий план AI-команды" in message.text for message in bot.messages)
        assert all("fake:final_composer" not in message.text for message in bot.messages)
        assert any("Файлы результата сохранены" in message.text for message in bot.messages)
        assert {document.filename for document in bot.documents} >= {
            "final_answer.md",
            "index.md",
        }

    asyncio.run(scenario())


def test_telegram_status_returns_runtime_status() -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(bot=bot, config=TelegramTeamBridgeConfig(provider="fake"))
        first = await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        completed = await asyncio.wait_for(bridge.jobs.wait("100"), timeout=1)
        await bridge.drain_watchers()

        response = await bridge.handle_text(chat_id=100, text="/status")

        assert response.decision.intent.value == "status_request"
        assert first.run_id == completed.job_id
        assert completed.run_id in bot.messages[-1].text
        assert "Последний run: completed." in bot.messages[-1].text
        assert "artifacts:" in bot.messages[-1].text
        assert "primary_artifact:" in bot.messages[-1].text

    asyncio.run(scenario())


def test_telegram_status_sees_active_job() -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        provider = PausingTeamProvider()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(provider="fake"),
            provider_factory=lambda: provider,
        )

        await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        await asyncio.wait_for(provider.started.wait(), timeout=1)
        response = await bridge.handle_text(chat_id=100, text="/status")

        assert response.status == TeamRuntimeStatus.RUNNING
        assert "Активная задача" in bot.messages[-1].text
        assert "provider: fake" in bot.messages[-1].text
        assert "started_at:" in bot.messages[-1].text
        assert "run_id: team_run_" in bot.messages[-1].text
        assert "ещё не создан" not in bot.messages[-1].text
        assert "current_agent: coordinator" in bot.messages[-1].text

        provider.release.set()
        await bridge.jobs.wait("100")

    asyncio.run(scenario())


def test_telegram_stopall_stops_active_runs() -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        provider = PausingTeamProvider()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(provider="fake"),
            provider_factory=lambda: provider,
        )

        await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        await asyncio.wait_for(provider.started.wait(), timeout=1)
        response = await bridge.handle_text(chat_id=100, text="/stopall")

        assert response.status == TeamRuntimeStatus.CANCELLED
        assert bridge.jobs.snapshot("100").status == TeamJobStatus.CANCELLED
        assert bot.messages[-1].text == (
            "Остановил активную задачу. Команда вернулась в общий чат."
        )

    asyncio.run(scenario())


def test_telegram_stopall_without_active_job_is_human_readable() -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(bot=bot, config=TelegramTeamBridgeConfig(provider="fake"))

        response = await bridge.handle_text(chat_id=100, text="/stopall")

        assert response.status == TeamRuntimeStatus.CANCELLED
        assert bot.messages[-1].text == "Активных задач сейчас нет."

    asyncio.run(scenario())


def test_telegram_rejects_second_task_when_job_is_active() -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        provider = PausingTeamProvider()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(provider="fake"),
            provider_factory=lambda: provider,
        )

        await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        await asyncio.wait_for(provider.started.wait(), timeout=1)
        response = await bridge.handle_text(chat_id=100, text="напиши второй план")

        assert response.status == TeamRuntimeStatus.RUNNING
        assert "задача уже выполняется" in bot.messages[-1].text.lower()

        provider.release.set()
        await bridge.jobs.wait("100")

    asyncio.run(scenario())


def test_telegram_failed_background_job_is_saved_as_last_failed() -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(provider="fake"),
            provider_factory=FailingTeamProvider,
        )

        response = await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        failed = await asyncio.wait_for(bridge.jobs.wait("100"), timeout=1)
        await bridge.drain_watchers()

        assert response.status == TeamRuntimeStatus.RUNNING
        assert failed.status == TeamJobStatus.FAILED
        assert bridge.jobs.last_failed("100").job_id == failed.job_id
        assert any("Можно продолжить" in message.text for message in bot.messages)

    asyncio.run(scenario())


def test_telegram_error_message_hides_traceback_from_main_and_logs_details(tmp_path) -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(
                provider="fake",
                workspace_root=tmp_path / "team_runs",
                log_chat_id=200,
            ),
            provider_factory=TracebackLikeFailingProvider,
        )

        await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        failed = await asyncio.wait_for(bridge.jobs.wait("100"), timeout=1)
        await bridge.drain_watchers()

        main_texts = [
            message.text
            for message in bot.messages
            if message.channel == TeamMessageChannel.MAIN_CHAT
        ]
        log_texts = [
            message.text
            for message in bot.messages
            if message.channel == TeamMessageChannel.LOG_CHAT
        ]
        assert failed.status == TeamJobStatus.FAILED
        assert all("Traceback" not in text for text in main_texts)
        assert all("secret.py" not in text for text in main_texts)
        assert any("Команда завершилась с ошибкой" in text for text in main_texts)
        assert any("run_id:" in text for text in main_texts)
        assert any("workspace:" in text for text in main_texts)
        assert any("run_failed" in text for text in log_texts)
        assert any("controlled low-level failure" in text for text in log_texts)

    asyncio.run(scenario())


def test_telegram_bridge_with_attachment_without_task_waits_for_instruction(tmp_path) -> None:
    async def scenario() -> None:
        file_path = tmp_path / "task.md"
        file_path.write_text("Контекст из вложения", encoding="utf-8")
        provider = FakeTeamProvider()
        attachments = TeamAttachmentProcessor().prepare_paths([file_path], source="test")
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(provider="fake"),
            provider_factory=lambda: provider,
        )

        response = await bridge.handle_text(chat_id=100, text="", attachments=attachments)

        assert response.status == TeamRuntimeStatus.IDLE
        assert response.decision.intent.value == "file_task"
        assert response.decision.should_start_run is False
        assert provider.calls == []
        assert any("файл вижу" in message.text for message in bot.messages)

    asyncio.run(scenario())


def test_telegram_bridge_message_attachment_without_task_waits_for_instruction(tmp_path) -> None:
    async def scenario() -> None:
        class Chat:
            id = 100

        class Message:
            chat = Chat()
            text = ""

        file_path = tmp_path / "telegram-note.txt"
        file_path.write_text("Telegram file context", encoding="utf-8")
        provider = FakeTeamProvider()
        Message.team_attachments = TeamAttachmentProcessor().prepare_paths(
            [file_path],
            source="telegram_test",
        )
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(provider="fake"),
            provider_factory=lambda: provider,
        )

        response = await bridge.handle_message(Message())

        assert response.decision.intent.value == "file_task"
        assert response.status == TeamRuntimeStatus.IDLE
        assert provider.calls == []
        assert any("файл вижу" in message.text for message in bot.messages)

    asyncio.run(scenario())


def test_telegram_bridge_file_with_caption_starts_run_and_saves_attachment(tmp_path) -> None:
    async def scenario() -> None:
        class Chat:
            id = 100

        class Message:
            chat = Chat()
            text = None
            caption = "проверь файл и сделай краткий вывод"

        file_path = tmp_path / "telegram-brief.md"
        file_path.write_text("Контент из Telegram", encoding="utf-8")
        Message.team_attachments = TeamAttachmentProcessor().prepare_paths(
            [file_path],
            source="telegram_test",
        )
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(
                provider="fake",
                workspace_root=tmp_path / "team_runs",
            ),
        )

        response = await bridge.handle_message(Message())
        completed = await asyncio.wait_for(bridge.jobs.wait("100"), timeout=1)
        await bridge.drain_watchers()

        assert response.status == TeamRuntimeStatus.RUNNING
        assert completed.status == TeamJobStatus.COMPLETED
        assert completed.workspace_path is not None
        assert (completed.workspace_path / "input_files" / "telegram-brief.md").exists()
        assert "Контент из Telegram" in (completed.workspace_path / "attachments.md").read_text(
            encoding="utf-8"
        )

    asyncio.run(scenario())


def test_telegram_denied_chat_gets_safe_response_and_no_job() -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(provider="fake", allowed_chat_ids=(200,)),
        )

        response = await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")

        assert response is None
        assert bridge.jobs.snapshot("100") is None
        assert bot.messages[-1].text == "Этот чат не подключён к AI-команде."

    asyncio.run(scenario())


def test_telegram_bridge_retries_send_message_after_group_migration(caplog) -> None:
    async def scenario() -> None:
        old_chat_id = -5218150544
        new_chat_id = -1003721761135
        bot = MigratingSendMessageBot(old_chat_id=old_chat_id, new_chat_id=new_chat_id)
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(
                provider="fake",
                allowed_chat_ids=(old_chat_id,),
            ),
        )

        response = await bridge.handle_text(chat_id=old_chat_id, text="/help")

        assert response is not None
        assert [call["chat_id"] for call in bot.message_calls[:2]] == [
            old_chat_id,
            new_chat_id,
        ]
        assert bridge._resolve_migrated_chat_id(old_chat_id) == new_chat_id
        assert bridge._chat_allowed(new_chat_id) is True

        next_response = await bridge.handle_text(chat_id=new_chat_id, text="/health")

        assert next_response is not None
        assert bot.message_calls[-1]["chat_id"] == new_chat_id
        assert "Telegram chat migrated" in caplog.text

    asyncio.run(scenario())


def test_telegram_team_message_sink_routes_main_and_log_messages() -> None:
    sink = TelegramTeamMessageSink(chat_id=100, log_chat_id=200)
    main_message = TeamMessage(
        run_id="team_run_1",
        channel=TeamMessageChannel.MAIN_CHAT,
        type=TeamMessageType.AGENT_STARTED,
        text="Босс, принял задачу.",
        author_name="Артём",
        author_role=AgentRole.COORDINATOR,
    )
    log_message = TeamMessage(
        run_id="team_run_1",
        channel=TeamMessageChannel.LOG_CHAT,
        type=TeamMessageType.SYSTEM_LOG,
        text="agent_started",
        author_name="Лог",
        author_role=AgentRole.COORDINATOR,
        metadata={"event_type": "agent_started"},
    )

    sink.publish(main_message)
    sink.publish(log_message)

    assert sink.outbox[0].chat_id == 100
    assert sink.outbox[0].text == "[Артём] Босс, принял задачу."
    assert sink.outbox[1].chat_id == 200
    assert sink.outbox[1].text == (
        "[Лог] agent_started (event_type=agent_started; run_id=team_run_1)"
    )


def test_telegram_bridge_sends_dialogue_to_main_chat_and_events_to_log_chat() -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(provider="fake", log_chat_id=200),
        )

        await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        await asyncio.wait_for(bridge.jobs.wait("100"), timeout=1)
        await bridge.drain_watchers()

        main_texts = [
            message.text
            for message in bot.messages
            if message.channel == TeamMessageChannel.MAIN_CHAT
        ]
        log_texts = [
            message.text
            for message in bot.messages
            if message.channel == TeamMessageChannel.LOG_CHAT
        ]
        assert any("[Артём] Босс, вижу задачу" in text for text in main_texts)
        assert any("[Саша] Финал собираю" in text for text in main_texts)
        assert any("[Лог] Командный run начат." in text for text in log_texts)
        assert any("[Лог] Финальный сборщик подготовил ответ." in text for text in log_texts)
        assert all("Командный run начат" not in text for text in main_texts)

    asyncio.run(scenario())


def test_telegram_bridge_does_not_send_log_messages_to_main_chat_without_log_chat() -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(bot=bot, config=TelegramTeamBridgeConfig(provider="fake"))

        await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        await asyncio.wait_for(bridge.jobs.wait("100"), timeout=1)
        await bridge.drain_watchers()

        assert all(message.channel != TeamMessageChannel.LOG_CHAT for message in bot.messages)
        assert all("[Лог]" not in message.text for message in bot.messages)

    asyncio.run(scenario())


def test_telegram_bridge_sends_typing_before_live_messages_and_final() -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(provider="fake", send_typing=True),
        )

        await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        await asyncio.wait_for(bridge.jobs.wait("100"), timeout=1)
        await bridge.drain_watchers()

        assert bot.chat_actions
        assert any(
            action.chat_id == 100 and action.action == "typing" for action in bot.chat_actions
        )

    asyncio.run(scenario())


def test_telegram_status_includes_completed_workspace_path(tmp_path) -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(
                provider="fake",
                workspace_root=tmp_path / "team_runs",
            ),
        )

        completed = await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        snapshot = await asyncio.wait_for(bridge.jobs.wait("100"), timeout=1)
        response = await bridge.handle_text(chat_id=100, text="/status")

        assert completed.run_id == snapshot.job_id
        assert response.run_id == snapshot.run_id
        assert str(snapshot.workspace_path) in bot.messages[-1].text

    asyncio.run(scenario())


def test_telegram_bridge_fake_provider_does_not_import_nodriver_adapter() -> None:
    async def scenario() -> None:
        sys.modules.pop("astra_nexus.team.nodriver_provider", None)
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(bot=bot, config=TelegramTeamBridgeConfig(provider="fake"))

        await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        await bridge.jobs.wait("100")

        assert "astra_nexus.team.nodriver_provider" not in sys.modules

    asyncio.run(scenario())


def test_telegram_bot_cli_creates_dispatcher_without_real_polling() -> None:
    class FakeDispatcher:
        def __init__(self) -> None:
            self.routers = []
            self.polled = False

        def include_router(self, router) -> None:  # noqa: ANN001
            self.routers.append(router)

        async def start_polling(self, bot) -> None:  # noqa: ANN001
            self.polled = True

    created = {}

    def dispatcher_factory() -> FakeDispatcher:
        dispatcher = FakeDispatcher()
        created["dispatcher"] = dispatcher
        return dispatcher

    settings = Settings(telegram_bot_token=SecretStr("123:fake-token"))

    exit_code = main_bot(
        ["--dry-run"],
        settings=settings,
        dispatcher_factory=dispatcher_factory,
        bot_factory=RecordingTelegramBot,
    )

    dispatcher = created["dispatcher"]
    assert exit_code == 0
    assert dispatcher.routers
    assert dispatcher.polled is False


def test_telegram_bot_dry_run_does_not_require_token(capsys) -> None:
    settings = Settings(telegram_bot_token=None)

    exit_code = main_bot(["--dry-run"], settings=settings)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "dry-run" in output.lower()


def test_telegram_preview_cli_uses_fake_provider_without_nodriver(capsys) -> None:
    exit_code = main_preview(["сделай краткий план AI-команды"])

    output = capsys.readouterr().out
    source = inspect.getsource(telegram_bridge_module)
    assert exit_code == 0
    assert "[Основной чат]" in output
    assert "сделай краткий план AI-команды" in output
    assert "fake:final_composer" not in output
    assert "from astra_nexus.team.nodriver_provider import" not in source


def test_telegram_job_preview_cli_runs_multiple_messages(capsys) -> None:
    exit_code = main_job_preview(["сделай краткий план AI-команды", "/status", "/stopall"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "> сделай краткий план AI-команды" in output
    assert "> /status" in output
    assert "сделай краткий план AI-команды" in output
    assert "fake:final_composer" not in output


def test_telegram_live_preview_cli_simulates_main_and_log_chats(capsys) -> None:
    exit_code = main_live_preview([])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "> брат че думаешь" in output
    assert "> /status" in output
    assert "> /stopall" in output
    assert "[Основной чат]" in output
    assert "[Лог]" in output
    assert "файл вижу" in output
