from __future__ import annotations

import asyncio
import inspect
import sys

from pydantic import SecretStr

from astra_nexus.config.settings import Settings
from astra_nexus.team import (
    AgentRole,
    TeamActiveRun,
    TeamMessage,
    TeamMessageChannel,
    TeamMessageType,
    TeamRuntimeStatus,
)
from astra_nexus.team import telegram_bridge as telegram_bridge_module
from astra_nexus.team.telegram_bridge import (
    RecordingTelegramBot,
    TelegramTeamBridge,
    TelegramTeamBridgeConfig,
    TelegramTeamMessageSink,
    main_bot,
    main_preview,
)


def test_telegram_preview_casual_does_not_create_run() -> None:
    bot = RecordingTelegramBot()
    bridge = TelegramTeamBridge(bot=bot, config=TelegramTeamBridgeConfig(provider="fake"))

    response = asyncio.run(bridge.handle_text(chat_id=100, text="брат че думаешь"))

    assert response.decision.intent.value == "casual_chat"
    assert response.status == TeamRuntimeStatus.IDLE
    assert bridge.registry.get(100).state.last_run_id is None
    assert bot.messages[-1].text == "Понял, это обычный диалог, команду не запускаю."


def test_telegram_preview_task_creates_completed_run() -> None:
    bot = RecordingTelegramBot()
    bridge = TelegramTeamBridge(bot=bot, config=TelegramTeamBridgeConfig(provider="fake"))

    response = asyncio.run(bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды"))

    assert response.status == TeamRuntimeStatus.COMPLETED
    assert response.run_id is not None
    assert bridge.registry.get(100).state.last_completed_run_id == response.run_id
    assert any("fake:final_composer" in message.text for message in bot.messages)


def test_telegram_status_returns_runtime_status() -> None:
    bot = RecordingTelegramBot()
    bridge = TelegramTeamBridge(bot=bot, config=TelegramTeamBridgeConfig(provider="fake"))
    first = asyncio.run(bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды"))

    response = asyncio.run(bridge.handle_text(chat_id=100, text="/status"))

    assert response.decision.intent.value == "status_request"
    assert first.run_id in bot.messages[-1].text
    assert "Последний завершённый run" in bot.messages[-1].text


def test_telegram_stopall_stops_active_runs() -> None:
    bot = RecordingTelegramBot()
    bridge = TelegramTeamBridge(bot=bot, config=TelegramTeamBridgeConfig(provider="fake"))
    controller = bridge.registry.get(100)
    controller.state.active_runs["team_run_active"] = TeamActiveRun(run_id="team_run_active")

    response = asyncio.run(bridge.handle_text(chat_id=100, text="/stopall"))

    assert response.status == TeamRuntimeStatus.CANCELLED
    assert controller.state.active_runs == {}
    assert controller.state.stopped_runs["team_run_active"].stop_requested is True
    assert "Остановил активные runs: team_run_active" in bot.messages[-1].text


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
    sys.modules.pop("astra_nexus.team.nodriver_provider", None)
    bot = RecordingTelegramBot()
    bridge = TelegramTeamBridge(bot=bot, config=TelegramTeamBridgeConfig(provider="fake"))

    asyncio.run(bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды"))

    assert "astra_nexus.team.nodriver_provider" not in sys.modules


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
