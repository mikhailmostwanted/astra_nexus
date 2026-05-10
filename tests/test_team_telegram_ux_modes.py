from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

from astra_nexus.team import AgentRole, TeamMessageChannel
from astra_nexus.team.fake_provider import FakeTeamProvider
from astra_nexus.team.models import AgentProfile, AgentResult
from astra_nexus.team.prompting import AgentPrompt
from astra_nexus.team.telegram_bridge import (
    RecordingTelegramBot,
    TelegramTeamBridge,
    TelegramTeamBridgeConfig,
    TelegramTeamMessageSink,
)


class HumanResultProvider(FakeTeamProvider):
    async def generate(
        self,
        *,
        profile: AgentProfile,
        user_task: str,
        previous_results: Sequence[AgentResult],
        prompt: AgentPrompt | None = None,
    ) -> str:
        return f"{profile.role.value} реальный результат для пользователя"


def main_texts(bot: RecordingTelegramBot) -> list[str]:
    return [
        message.text for message in bot.messages if message.channel == TeamMessageChannel.MAIN_CHAT
    ]


def log_texts(bot: RecordingTelegramBot) -> list[str]:
    return [
        message.text for message in bot.messages if message.channel == TeamMessageChannel.LOG_CHAT
    ]


def test_minimal_atmosphere_for_nodriver_omits_template_agent_phrases(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(
                provider="nodriver",
                workspace_root=tmp_path / "runs",
                atmosphere_mode="minimal",
                log_chat_id=200,
            ),
            provider_factory=HumanResultProvider,
        )

        await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        await asyncio.wait_for(bridge.jobs.wait("100"), timeout=1)
        await bridge.drain_watchers()

        texts = main_texts(bot)
        assert "Принял задачу. Команда начала работу." in texts
        assert all("Босс, вижу задачу" not in text for text in texts)
        assert all("Маршрут готов" not in text for text in texts)
        assert any("final_composer реальный результат" in text for text in texts)
        assert any("run_started" in text for text in log_texts(bot))

    asyncio.run(scenario())


def test_result_snippet_mode_uses_real_agent_result_not_templates(tmp_path: Path) -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(
                provider="nodriver",
                workspace_root=tmp_path / "runs",
                atmosphere_mode="result_snippet",
                atmosphere_snippet_max_chars=48,
            ),
            provider_factory=HumanResultProvider,
        )

        await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        await asyncio.wait_for(bridge.jobs.wait("100"), timeout=1)
        await bridge.drain_watchers()

        texts = main_texts(bot)
        assert any("coordinator реальный результат" in text for text in texts)
        assert all("Маршрут готов" not in text for text in texts)
        assert all(len(text) <= 90 for text in texts if "реальный результат" in text)

    asyncio.run(scenario())


def test_main_chat_dedupe_removes_identical_human_messages() -> None:
    sink = TelegramTeamMessageSink(chat_id=100, session_id="s1")

    sink.publish_human_text(
        run_id="run1",
        agent_role=AgentRole.EDITOR,
        phase="completed",
        text="Одинаковый текст",
    )
    sink.publish_human_text(
        run_id="run1",
        agent_role=AgentRole.EDITOR,
        phase="completed",
        text="  Одинаковый   текст  ",
    )

    assert [message.text for message in sink.outbox] == ["Одинаковый текст"]


def test_completed_job_final_answer_is_clean_and_not_fake_marker(tmp_path: Path) -> None:
    async def scenario() -> None:
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(
                provider="fake",
                workspace_root=tmp_path / "runs",
                atmosphere_mode="minimal",
            ),
        )

        await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        await asyncio.wait_for(bridge.jobs.wait("100"), timeout=1)
        await bridge.drain_watchers()

        texts = main_texts(bot)
        assert any("сделай краткий план AI-команды" in text for text in texts)
        assert all("fake:final_composer" not in text for text in texts)
        assert all("Ты агент в системе" not in text for text in texts)

    asyncio.run(scenario())
