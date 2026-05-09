from __future__ import annotations

import argparse
import asyncio

from astra_nexus.team.fake_provider import FakeTeamProvider
from astra_nexus.team.messages import (
    InMemoryTeamMessageSink,
    TeamMessage,
    TeamMessageChannel,
)
from astra_nexus.team.orchestrator import AsyncTeamOrchestrator

DEFAULT_TASK = "Проверь идею AI-команды для Astra Nexus."


async def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    user_task = " ".join(args.task).strip() or DEFAULT_TASK
    sink = InMemoryTeamMessageSink()
    orchestrator = AsyncTeamOrchestrator(
        provider=FakeTeamProvider(),
        message_sink=sink,
    )

    await orchestrator.run(user_task)

    for message in _visible_messages(sink.messages, main_only=args.main_only):
        print(f"[{_label(message)}] {message.text}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview AI Team chat stream with fake provider.")
    parser.add_argument(
        "task",
        nargs="*",
        help="Текст задачи. Если не указан, используется дефолтная задача.",
    )
    parser.add_argument(
        "--main-only",
        action="store_true",
        help="Показывать только main_chat без технического log_chat.",
    )
    return parser.parse_args(argv)


def _visible_messages(
    messages: list[TeamMessage],
    *,
    main_only: bool,
) -> list[TeamMessage]:
    if main_only:
        return [message for message in messages if message.channel == TeamMessageChannel.MAIN_CHAT]
    return [
        message
        for message in messages
        if message.channel in {TeamMessageChannel.MAIN_CHAT, TeamMessageChannel.LOG_CHAT}
    ]


def _label(message: TeamMessage) -> str:
    if message.channel == TeamMessageChannel.LOG_CHAT:
        return "Лог"
    return message.author_name or "Команда"


if __name__ == "__main__":
    raise SystemExit(main())
