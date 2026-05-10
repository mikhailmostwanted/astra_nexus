from __future__ import annotations

import asyncio
import inspect

from astra_nexus.config.settings import Settings
from astra_nexus.team import atmosphere_preview as atmosphere_preview_module
from astra_nexus.team.atmosphere import (
    AtmosphereProfile,
    AtmosphereTeamMessageSink,
    TeamAtmosphereRenderer,
)
from astra_nexus.team.dialogue import build_agent_start_turn
from astra_nexus.team.messages import (
    InMemoryTeamMessageSink,
    TeamMessage,
    TeamMessageChannel,
    TeamMessageType,
)
from astra_nexus.team.models import AgentRole, RunEvent, RunEventType
from astra_nexus.team.profiles import default_profiles_by_role
from astra_nexus.team.telegram_bridge import (
    RecordingTelegramBot,
    TelegramTeamBridge,
    TelegramTeamBridgeConfig,
)


def test_atmosphere_renderer_creates_human_main_messages() -> None:
    renderer = TeamAtmosphereRenderer()
    turn = build_agent_start_turn(
        run_id="team_run_1",
        profile=default_profiles_by_role()[AgentRole.COORDINATOR],
    )

    messages = renderer.render_dialogue_turn(turn)

    assert messages[0].channel == TeamMessageChannel.MAIN_CHAT
    assert messages[0].text == "Босс, вижу задачу. Сначала разложу её на части."
    assert messages[0].author_role == AgentRole.COORDINATOR


def test_atmosphere_suppresses_technical_events_in_main_chat() -> None:
    renderer = TeamAtmosphereRenderer(AtmosphereProfile(suppress_technical_in_main=True))
    event = RunEvent(
        run_id="team_run_1",
        type=RunEventType.AGENT_FAILED,
        message="agent failed",
        agent_role=AgentRole.CRITIC,
    )

    messages = renderer.render_event(event)

    assert [message.channel for message in messages] == [TeamMessageChannel.LOG_CHAT]


def test_atmosphere_log_chat_gets_technical_events() -> None:
    renderer = TeamAtmosphereRenderer()
    event = RunEvent(
        run_id="team_run_1",
        type=RunEventType.AGENT_STARTED,
        message="agent started",
        agent_role=AgentRole.ANALYST,
        payload={"provider": "fake", "execution_mode": "sequential"},
    )

    messages = renderer.render_event(event)

    assert messages[0].channel == TeamMessageChannel.LOG_CHAT
    assert messages[0].is_technical is True
    assert messages[0].metadata["provider"] == "fake"


def test_atmosphere_message_budget_limits_main_messages_but_keeps_final_signal() -> None:
    inner = InMemoryTeamMessageSink()
    sink = AtmosphereTeamMessageSink(
        inner,
        renderer=TeamAtmosphereRenderer(AtmosphereProfile(max_main_messages_per_run=1)),
    )
    first = TeamMessage(
        run_id="team_run_1",
        channel=TeamMessageChannel.MAIN_CHAT,
        type=TeamMessageType.AGENT_SAYS,
        text="first",
        author_name="Артём",
        author_role=AgentRole.COORDINATOR,
        metadata={"style": "working", "phase": "coordination"},
    )
    second = TeamMessage(
        run_id="team_run_1",
        channel=TeamMessageChannel.MAIN_CHAT,
        type=TeamMessageType.AGENT_SAYS,
        text="second",
        author_name="Ирина",
        author_role=AgentRole.ANALYST,
        metadata={"style": "working", "phase": "analysis"},
    )
    final = TeamMessage(
        run_id="team_run_1",
        channel=TeamMessageChannel.MAIN_CHAT,
        type=TeamMessageType.AGENT_SAYS,
        text="Финальный ответ собран.",
        author_name="Саша",
        author_role=AgentRole.FINAL_COMPOSER,
        metadata={"style": "summary", "phase": "finalization"},
    )

    sink.publish(first)
    sink.publish(second)
    sink.publish(final)

    assert [message.author_role for message in inner.messages] == [
        AgentRole.COORDINATOR,
        AgentRole.FINAL_COMPOSER,
    ]
    assert inner.messages[-1].text == "Финал готов. Ниже собранный вариант."


def test_atmosphere_settings_defaults_are_correct() -> None:
    settings = Settings(_env_file=None)

    assert settings.team_atmosphere_enabled is True
    assert settings.team_atmosphere_level == "normal"
    assert settings.team_atmosphere_send_delays is False
    assert settings.team_atmosphere_min_delay_seconds == 0.3
    assert settings.team_atmosphere_max_delay_seconds == 1.4
    assert settings.team_atmosphere_emoji_enabled is False
    assert settings.team_atmosphere_max_main_messages_per_run == 20
    assert settings.team_atmosphere_suppress_technical_in_main is True


def test_atmosphere_preview_cli_runs_without_telegram_api_or_nodriver(capsys) -> None:
    exit_code = atmosphere_preview_module.main([])

    output = capsys.readouterr().out
    source = inspect.getsource(atmosphere_preview_module)
    assert exit_code == 0
    assert "MAIN CHAT" in output
    assert "LOG CHAT" in output
    assert "Босс, вижу задачу" in output
    assert "Босс, файл вижу" in output
    assert "NoDriver" not in source
    assert "nodriver" not in source


def test_telegram_atmosphere_budget_keeps_final_message_in_main_chat() -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(
                provider="fake",
                atmosphere=AtmosphereProfile(max_main_messages_per_run=1),
            ),
        )

        await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        await asyncio.wait_for(bridge.jobs.wait("100"), timeout=1)
        await bridge.drain_watchers()

        main_texts = [
            message.text
            for message in bot.messages
            if message.channel == TeamMessageChannel.MAIN_CHAT
        ]
        assert any("Босс, вижу задачу" in text for text in main_texts)
        assert any("сделай краткий план AI-команды" in text for text in main_texts)
        assert all("fake:final_composer" not in text for text in main_texts)

    asyncio.run(scenario())
