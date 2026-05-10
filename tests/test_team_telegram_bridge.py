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


def test_telegram_preview_casual_does_not_create_run() -> None:
    bot = RecordingTelegramBot()
    bridge = TelegramTeamBridge(bot=bot, config=TelegramTeamBridgeConfig(provider="fake"))

    response = asyncio.run(bridge.handle_text(chat_id=100, text="брат че думаешь"))

    assert response.decision.intent.value == "casual_chat"
    assert response.status == TeamRuntimeStatus.IDLE
    assert bridge.registry.get(100).state.last_run_id is None
    assert bot.messages[-1].text == "Понял, это обычный диалог, команду не запускаю."


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
            message.text == "Принял задачу. Команда начала работу." for message in bot.messages
        )

        provider.release.set()
        completed = await asyncio.wait_for(bridge.jobs.wait("100"), timeout=1)

        assert completed.status == TeamJobStatus.COMPLETED
        assert bridge.registry.get(100).state.last_completed_run_id == completed.run_id
        assert any("fake:final_composer" in message.text for message in bot.messages)

    asyncio.run(scenario())


def test_telegram_status_returns_runtime_status() -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(bot=bot, config=TelegramTeamBridgeConfig(provider="fake"))
        first = await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        completed = await asyncio.wait_for(bridge.jobs.wait("100"), timeout=1)

        response = await bridge.handle_text(chat_id=100, text="/status")

        assert response.decision.intent.value == "status_request"
        assert first.run_id == completed.job_id
        assert completed.run_id in bot.messages[-1].text
        assert "Последняя завершённая задача" in bot.messages[-1].text

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
        assert "Команда остановлена" in bot.messages[-1].text

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

        assert response.status == TeamRuntimeStatus.RUNNING
        assert failed.status == TeamJobStatus.FAILED
        assert bridge.jobs.last_failed("100").job_id == failed.job_id
        assert any("Можно продолжить" in message.text for message in bot.messages)

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
    assert sink.outbox[1].text == "[Лог] agent_started"


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


def test_telegram_preview_cli_uses_fake_provider_without_nodriver(capsys) -> None:
    exit_code = main_preview(["сделай краткий план AI-команды"])

    output = capsys.readouterr().out
    source = inspect.getsource(telegram_bridge_module)
    assert exit_code == 0
    assert "[Основной чат]" in output
    assert "fake:final_composer" in output
    assert "from astra_nexus.team.nodriver_provider import" not in source


def test_telegram_job_preview_cli_runs_multiple_messages(capsys) -> None:
    exit_code = main_job_preview(["сделай краткий план AI-команды", "/status", "/stopall"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "> сделай краткий план AI-команды" in output
    assert "> /status" in output
    assert "fake:final_composer" in output
