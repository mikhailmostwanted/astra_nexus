from __future__ import annotations

import argparse
import asyncio
import importlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from astra_nexus.config.settings import Settings, load_settings
from astra_nexus.team.fake_provider import FakeTeamProvider
from astra_nexus.team.messages import TeamMessage, TeamMessageChannel, TeamMessageSink
from astra_nexus.team.provider import TeamProvider
from astra_nexus.team.runtime import TeamConversationController, TeamRuntimeResponse
from astra_nexus.team.workspace import TeamRunWorkspace
from astra_nexus.utils.logging import configure_logging

DEFAULT_TELEGRAM_PREVIEW_MESSAGE = "брат че думаешь"
DEFAULT_PROVIDER = "fake"


@dataclass(frozen=True)
class TelegramTeamBridgeConfig:
    provider: str = DEFAULT_PROVIDER
    workspace_root: Path = Path("data/team_runs")
    log_chat_id: int | None = None
    allowed_chat_ids: tuple[int, ...] = ()

    @classmethod
    def from_settings(cls, settings: Settings) -> TelegramTeamBridgeConfig:
        return cls(
            provider=settings.team_telegram_provider,
            workspace_root=settings.team_runs_dir,
            log_chat_id=settings.team_telegram_log_chat_id,
            allowed_chat_ids=_parse_allowed_chat_ids(settings.team_telegram_allowed_chat_ids),
        )


@dataclass(frozen=True)
class TelegramOutgoingMessage:
    chat_id: int
    text: str
    channel: TeamMessageChannel = TeamMessageChannel.MAIN_CHAT


class RecordingTelegramBot:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.messages: list[TelegramOutgoingMessage] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> TelegramOutgoingMessage:
        message = TelegramOutgoingMessage(
            chat_id=chat_id,
            text=text,
            channel=kwargs.get("channel", TeamMessageChannel.MAIN_CHAT),
        )
        self.messages.append(message)
        return message


class TelegramTeamMessageSink(TeamMessageSink):
    def __init__(self, *, chat_id: int, log_chat_id: int | None = None) -> None:
        self.chat_id = chat_id
        self.log_chat_id = log_chat_id
        self.outbox: list[TelegramOutgoingMessage] = []

    def publish(self, message: TeamMessage) -> None:
        if message.channel == TeamMessageChannel.DEBUG and self.log_chat_id is None:
            return
        self.outbox.append(
            TelegramOutgoingMessage(
                chat_id=self._target_chat_id(message.channel),
                text=self.render(message),
                channel=message.channel,
            )
        )

    def render(self, message: TeamMessage) -> str:
        if message.channel == TeamMessageChannel.LOG_CHAT:
            author = "Лог"
        elif message.channel == TeamMessageChannel.DEBUG:
            author = "Debug"
        else:
            author = message.author_name or "Команда"
        return f"[{author}] {message.text}"

    def pop_outbox(self) -> list[TelegramOutgoingMessage]:
        messages = list(self.outbox)
        self.outbox.clear()
        return messages

    def clear(self) -> None:
        self.outbox.clear()

    def _target_chat_id(self, channel: TeamMessageChannel) -> int:
        if channel in {TeamMessageChannel.LOG_CHAT, TeamMessageChannel.DEBUG}:
            return self.log_chat_id or self.chat_id
        return self.chat_id


class TelegramTeamLogSink(TelegramTeamMessageSink):
    def publish(self, message: TeamMessage) -> None:
        if message.channel not in {TeamMessageChannel.LOG_CHAT, TeamMessageChannel.DEBUG}:
            return
        super().publish(message)


@dataclass
class TelegramTeamSession:
    controller: TeamConversationController
    sink: TelegramTeamMessageSink


ProviderFactory = Callable[[], TeamProvider]


@dataclass
class TelegramTeamSessionRegistry:
    config: TelegramTeamBridgeConfig
    provider_factory: ProviderFactory = field(default_factory=lambda: _fake_provider_factory)
    sessions: dict[int, TelegramTeamSession] = field(default_factory=dict)

    def get(self, chat_id: int) -> TeamConversationController:
        return self.session(chat_id).controller

    def session(self, chat_id: int) -> TelegramTeamSession:
        if chat_id not in self.sessions:
            sink = TelegramTeamMessageSink(chat_id=chat_id, log_chat_id=self.config.log_chat_id)
            self.sessions[chat_id] = TelegramTeamSession(
                controller=TeamConversationController(
                    provider=self.provider_factory(),
                    workspace=TeamRunWorkspace(root_path=self.config.workspace_root),
                    message_sink=sink,
                ),
                sink=sink,
            )
        return self.sessions[chat_id]


class TelegramTeamBridge:
    def __init__(
        self,
        *,
        bot: Any,
        config: TelegramTeamBridgeConfig | None = None,
        provider_factory: ProviderFactory | None = None,
        registry: TelegramTeamSessionRegistry | None = None,
    ) -> None:
        self.bot = bot
        self.config = config or TelegramTeamBridgeConfig()
        self.provider_factory = provider_factory or _provider_factory(self.config.provider)
        self.registry = registry or TelegramTeamSessionRegistry(
            config=self.config,
            provider_factory=self.provider_factory,
        )

    async def handle_message(self, message: Any) -> TeamRuntimeResponse | None:
        chat_id = int(message.chat.id)
        text = _message_text(message)
        attachments_count = _attachments_count(message)
        return await self.handle_text(
            chat_id=chat_id,
            text=text,
            attachments_count=attachments_count,
        )

    async def handle_text(
        self,
        *,
        chat_id: int,
        text: str,
        attachments_count: int = 0,
    ) -> TeamRuntimeResponse | None:
        if not self._chat_allowed(chat_id):
            await self._send(
                TelegramOutgoingMessage(
                    chat_id=chat_id,
                    text="Этот чат не разрешён для AI-команды.",
                )
            )
            return None

        session = self.registry.session(chat_id)
        session.sink.clear()
        controller = session.controller
        response = await controller.handle(
            text,
            attachments_count=attachments_count,
            active_run_id=next(iter(controller.state.active_runs), None),
            last_run_id=controller.state.last_run_id,
            failed_run_id=controller.state.last_failed_run_id,
            has_active_run=bool(controller.state.active_runs),
        )

        outgoing_messages = session.sink.pop_outbox()
        outgoing_messages.append(
            TelegramOutgoingMessage(chat_id=chat_id, text=self._response_text(response))
        )
        for outgoing in outgoing_messages:
            await self._send(outgoing)
        return response

    async def _send(self, message: TelegramOutgoingMessage) -> None:
        if isinstance(self.bot, RecordingTelegramBot):
            await self.bot.send_message(
                chat_id=message.chat_id,
                text=message.text,
                channel=message.channel,
            )
            return
        await self.bot.send_message(chat_id=message.chat_id, text=message.text)

    def _chat_allowed(self, chat_id: int) -> bool:
        return not self.config.allowed_chat_ids or chat_id in self.config.allowed_chat_ids

    def _response_text(self, response: TeamRuntimeResponse) -> str:
        lines = [response.user_visible_reply]
        if response.workspace_path is not None and response.status.value == "failed":
            lines.append("")
            lines.append(f"workspace: {response.workspace_path}")
        if response.status.value == "failed" and response.run_id is not None:
            lines.append(f"Можно продолжить: astra-nexus-team-resume {response.run_id}")
        return "\n".join(line for line in lines if line)


async def run_preview(argv: list[str] | None = None) -> int:
    args = _parse_preview_args(argv)
    message = " ".join(args.message).strip() or DEFAULT_TELEGRAM_PREVIEW_MESSAGE
    settings = load_settings()
    config = TelegramTeamBridgeConfig(
        provider="fake",
        workspace_root=args.workspace_root or settings.team_runs_dir,
        log_chat_id=args.log_chat_id,
    )
    bot = RecordingTelegramBot()
    bridge = TelegramTeamBridge(bot=bot, config=config)
    await bridge.handle_text(chat_id=args.chat_id, text=message)

    for outgoing in bot.messages:
        label = "Лог" if outgoing.channel == TeamMessageChannel.LOG_CHAT else "Основной чат"
        print(f"[{label}] {outgoing.text}")
    return 0


def main_preview(argv: list[str] | None = None) -> int:
    return asyncio.run(run_preview(argv))


async def run_bot(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    dispatcher_factory: Callable[[], Any] | None = None,
    bot_factory: Callable[..., Any] | None = None,
) -> int:
    args = _parse_bot_args(argv)
    settings = settings or load_settings()
    configure_logging(settings.log_level)
    config = TelegramTeamBridgeConfig.from_settings(settings)
    token = _token_value(settings)

    if not token:
        print("Telegram bot token is not configured.")
        return 1

    bot = _build_bot(bot_factory, token)
    bridge = TelegramTeamBridge(bot=bot, config=config)
    dispatcher = dispatcher_factory() if dispatcher_factory is not None else _build_dispatcher()
    dispatcher.include_router(_build_router(bridge))

    print(
        "Astra Nexus AI Team Telegram bot configured: "
        f"provider={config.provider}, workspace_root={config.workspace_root}"
    )
    if args.dry_run:
        return 0

    try:
        await dispatcher.start_polling(bot)
    except KeyboardInterrupt:
        print("Telegram bot stopped.")
    return 0


def main_bot(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    dispatcher_factory: Callable[[], Any] | None = None,
    bot_factory: Callable[..., Any] | None = None,
) -> int:
    return asyncio.run(
        run_bot(
            argv,
            settings=settings,
            dispatcher_factory=dispatcher_factory,
            bot_factory=bot_factory,
        )
    )


def _parse_preview_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview Telegram AI Team bridge without Telegram."
    )
    parser.add_argument("message", nargs="*", help="Сообщение пользователя.")
    parser.add_argument("--chat-id", type=int, default=100)
    parser.add_argument("--log-chat-id", type=int, default=None)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Папка для team run workspaces.",
    )
    return parser.parse_args(argv)


def _parse_bot_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AI Team Telegram bot polling.")
    parser.add_argument("--dry-run", action="store_true", help="Configure bot without polling.")
    return parser.parse_args(argv)


def _build_router(bridge: TelegramTeamBridge) -> Any:
    from aiogram import Router

    router = Router()

    @router.message()
    async def handle_team_message(message: Any) -> None:
        await bridge.handle_message(message)

    return router


def _build_dispatcher() -> Any:
    from aiogram import Dispatcher

    return Dispatcher()


def _build_bot(bot_factory: Callable[..., Any] | None, token: str) -> Any:
    if bot_factory is None:
        from aiogram import Bot

        return Bot(token=token)
    for args, kwargs in (
        ((), {"token": token}),
        ((token,), {}),
        ((), {}),
    ):
        try:
            return bot_factory(*args, **kwargs)
        except TypeError:
            continue
    return bot_factory()


def _provider_factory(provider_name: str) -> ProviderFactory:
    normalized = provider_name.strip().lower()
    if normalized in {"", "fake"}:
        return _fake_provider_factory
    if normalized == "nodriver":
        return _nodriver_provider_factory
    raise ValueError(f"Unknown team telegram provider: {provider_name}")


def _fake_provider_factory() -> TeamProvider:
    return FakeTeamProvider()


def _nodriver_provider_factory() -> TeamProvider:
    module = importlib.import_module("astra_nexus.team.nodriver_provider")
    NoDriverTeamProvider = module.NoDriverTeamProvider
    return NoDriverTeamProvider(settings=load_settings())


def _parse_allowed_chat_ids(value: str | Iterable[int]) -> tuple[int, ...]:
    if isinstance(value, str):
        chunks = (chunk.strip() for chunk in value.split(","))
        return tuple(int(chunk) for chunk in chunks if chunk)
    return tuple(int(item) for item in value)


def _token_value(settings: Settings) -> str | None:
    token = settings.telegram_bot_token
    if token is None:
        return None
    return token.get_secret_value()


def _message_text(message: Any) -> str:
    return str(getattr(message, "text", None) or getattr(message, "caption", None) or "")


def _attachments_count(message: Any) -> int:
    attachment_fields = (
        "document",
        "photo",
        "video",
        "audio",
        "voice",
        "animation",
        "sticker",
    )
    count = 0
    for field_name in attachment_fields:
        value = getattr(message, field_name, None)
        if not value:
            continue
        if isinstance(value, list | tuple):
            count += len(value)
        else:
            count += 1
    return count


if __name__ == "__main__":
    raise SystemExit(main_preview())
