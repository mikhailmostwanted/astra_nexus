from __future__ import annotations

import argparse
import asyncio
import importlib
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from astra_nexus.config.settings import Settings, load_settings
from astra_nexus.team.attachments import (
    TeamAttachmentProcessor,
    TeamInputAttachment,
)
from astra_nexus.team.fake_provider import FakeTeamProvider
from astra_nexus.team.intake import TeamInput, TeamInputIntent, TeamIntakeDecision
from astra_nexus.team.jobs import (
    TeamJobAlreadyActiveError,
    TeamJobHandle,
    TeamJobManager,
    TeamJobSnapshot,
    TeamJobStatus,
)
from astra_nexus.team.messages import TeamMessage, TeamMessageChannel, TeamMessageSink
from astra_nexus.team.provider import TeamProvider
from astra_nexus.team.runtime import (
    TeamConversationController,
    TeamRuntimeResponse,
    TeamRuntimeStatus,
)
from astra_nexus.team.workspace import TeamRunWorkspace
from astra_nexus.utils.logging import configure_logging

DEFAULT_TELEGRAM_PREVIEW_MESSAGE = "брат че думаешь"
DEFAULT_PROVIDER = "fake"


@dataclass(frozen=True)
class TelegramTeamBridgeConfig:
    provider: str = DEFAULT_PROVIDER
    workspace_root: Path = Path("data/team_runs")
    uploads_root: Path = Path("data/team_telegram_downloads")
    attachment_max_files: int = 5
    attachment_max_bytes: int = 10 * 1024 * 1024
    attachment_text_max_chars: int = 20000
    log_chat_id: int | None = None
    allowed_chat_ids: tuple[int, ...] = ()
    environment: str = "local"
    send_typing: bool = True
    human_messages: bool = True

    @classmethod
    def from_settings(cls, settings: Settings) -> TelegramTeamBridgeConfig:
        max_file_size_mb = max(1, settings.team_telegram_max_file_size_mb)
        return cls(
            provider=settings.team_telegram_provider,
            workspace_root=settings.team_runs_dir,
            uploads_root=settings.team_telegram_downloads_dir,
            attachment_max_files=settings.team_attachments_max_files,
            attachment_max_bytes=max_file_size_mb * 1024 * 1024,
            attachment_text_max_chars=settings.team_attachment_text_max_chars,
            log_chat_id=settings.team_telegram_log_chat_id,
            allowed_chat_ids=_parse_allowed_chat_ids(settings.team_telegram_allowed_chat_ids),
            environment=settings.environment,
            send_typing=settings.team_telegram_send_typing,
            human_messages=settings.team_telegram_human_messages,
        )


@dataclass(frozen=True)
class TelegramOutgoingMessage:
    chat_id: int
    text: str
    channel: TeamMessageChannel = TeamMessageChannel.MAIN_CHAT
    send_typing: bool = False


@dataclass(frozen=True)
class TelegramChatAction:
    chat_id: int
    action: str


class RecordingTelegramBot:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.messages: list[TelegramOutgoingMessage] = []
        self.chat_actions: list[TelegramChatAction] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> TelegramOutgoingMessage:
        message = TelegramOutgoingMessage(
            chat_id=chat_id,
            text=text,
            channel=kwargs.get("channel", TeamMessageChannel.MAIN_CHAT),
            send_typing=kwargs.get("send_typing", False),
        )
        self.messages.append(message)
        return message

    async def send_chat_action(self, chat_id: int, action: str, **kwargs: Any) -> None:
        self.chat_actions.append(TelegramChatAction(chat_id=chat_id, action=action))


class _LivePreviewProvider(FakeTeamProvider):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()
        self._paused_once = False

    async def generate(self, **kwargs: Any) -> str:
        profile = kwargs["profile"]
        if profile.role.value == "coordinator" and not self._paused_once:
            self._paused_once = True
            await self.release.wait()
        return await super().generate(**kwargs)


class TelegramTeamMessageSink(TeamMessageSink):
    def __init__(
        self,
        *,
        chat_id: int,
        log_chat_id: int | None = None,
        human_messages: bool = True,
    ) -> None:
        self.chat_id = chat_id
        self.log_chat_id = log_chat_id
        self.human_messages = human_messages
        self.outbox: list[TelegramOutgoingMessage] = []
        self.auto_sender: Callable[[TelegramOutgoingMessage], None] | None = None

    def publish(self, message: TeamMessage) -> None:
        if message.channel in {TeamMessageChannel.LOG_CHAT, TeamMessageChannel.DEBUG}:
            if self.log_chat_id is None:
                return
        elif message.channel == TeamMessageChannel.MAIN_CHAT and not self.human_messages:
            return
        outgoing = TelegramOutgoingMessage(
            chat_id=self._target_chat_id(message.channel),
            text=self.render(message),
            channel=message.channel,
            send_typing=message.channel == TeamMessageChannel.MAIN_CHAT,
        )
        if self.auto_sender is not None:
            self.auto_sender(outgoing)
            return
        self.outbox.append(outgoing)

    def render(self, message: TeamMessage) -> str:
        if message.channel == TeamMessageChannel.LOG_CHAT:
            return self._render_log(message)
        elif message.channel == TeamMessageChannel.DEBUG:
            author = "Debug"
        else:
            author = message.author_name or "Команда"
        return f"[{author}] {message.text}"

    def _render_log(self, message: TeamMessage) -> str:
        details = []
        for key in ("event_type", "run_id", "job_id", "intent", "status", "workspace"):
            value = message.metadata.get(key)
            if value:
                details.append(f"{key}={value}")
        suffix = f" ({'; '.join(details)})" if details else ""
        return f"[Лог] {message.text}{suffix}"

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
            sink = TelegramTeamMessageSink(
                chat_id=chat_id,
                log_chat_id=self.config.log_chat_id,
                human_messages=self.config.human_messages,
            )
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
        jobs: TeamJobManager | None = None,
    ) -> None:
        self.bot = bot
        self.config = config or TelegramTeamBridgeConfig()
        self.provider_factory = provider_factory or _provider_factory(self.config.provider)
        self.registry = registry or TelegramTeamSessionRegistry(
            config=self.config,
            provider_factory=self.provider_factory,
        )
        self.jobs = jobs or TeamJobManager()
        self.attachment_processor = TeamAttachmentProcessor(
            max_files=self.config.attachment_max_files,
            max_bytes=self.config.attachment_max_bytes,
            text_max_chars=self.config.attachment_text_max_chars,
        )
        self._send_tasks: set[asyncio.Task[None]] = set()
        self._watch_tasks: set[asyncio.Task[None]] = set()

    async def handle_message(self, message: Any) -> TeamRuntimeResponse | None:
        chat_id = int(message.chat.id)
        text = _message_text(message)
        attachments = await self._attachments_from_message(chat_id=chat_id, message=message)
        return await self.handle_text(
            chat_id=chat_id,
            text=text,
            attachments=attachments,
        )

    async def handle_text(
        self,
        *,
        chat_id: int,
        text: str,
        attachments_count: int = 0,
        attachments: tuple[TeamInputAttachment, ...] = (),
    ) -> TeamRuntimeResponse | None:
        if not self._chat_allowed(chat_id):
            await self._send(
                TelegramOutgoingMessage(
                    chat_id=chat_id,
                    text="Этот чат не подключён к AI-команде.",
                )
            )
            return None

        session = self._session(chat_id)
        controller = session.controller
        team_input = self._team_input(
            controller=controller,
            text=text,
            attachments_count=attachments_count,
            attachments=attachments,
        )
        decision = controller.router.route(team_input)
        session_id = self._session_id(chat_id)

        if decision.intent == TeamInputIntent.STATUS_REQUEST:
            response = await self._handle_status(
                session_id=session_id,
                controller=controller,
                team_input=team_input,
                decision=decision,
            )
            await self._send(
                TelegramOutgoingMessage(chat_id=chat_id, text=response.user_visible_reply)
            )
            return response

        if decision.intent == TeamInputIntent.STOP_ALL:
            response = await self._handle_stopall(
                session_id=session_id,
                controller=controller,
                team_input=team_input,
                decision=decision,
            )
            await self._send(
                TelegramOutgoingMessage(chat_id=chat_id, text=response.user_visible_reply)
            )
            return response

        if self._should_start_job(decision):
            response = self._start_background_job(
                chat_id=chat_id,
                session_id=session_id,
                session=session,
                team_input=team_input,
                decision=decision,
            )
            await self._send(
                TelegramOutgoingMessage(chat_id=chat_id, text=response.user_visible_reply)
            )
            return response

        response = await controller.handle(team_input)

        outgoing_messages = session.sink.pop_outbox()
        outgoing_messages.append(
            TelegramOutgoingMessage(chat_id=chat_id, text=self._response_text(response))
        )
        for outgoing in outgoing_messages:
            await self._send(outgoing)
        return response

    async def drain_sends(self) -> None:
        while self._send_tasks:
            tasks = tuple(self._send_tasks)
            await asyncio.gather(*tasks, return_exceptions=True)
            self._send_tasks.difference_update(tasks)

    async def drain_watchers(self) -> None:
        while self._watch_tasks:
            tasks = tuple(self._watch_tasks)
            await asyncio.gather(*tasks, return_exceptions=True)
            self._watch_tasks.difference_update(tasks)

    async def _send(self, message: TelegramOutgoingMessage) -> None:
        if message.send_typing and self.config.send_typing:
            await self._send_typing(message.chat_id)
        if isinstance(self.bot, RecordingTelegramBot):
            await self.bot.send_message(
                chat_id=message.chat_id,
                text=message.text,
                channel=message.channel,
                send_typing=message.send_typing,
            )
            return
        await self.bot.send_message(chat_id=message.chat_id, text=message.text)

    async def _send_typing(self, chat_id: int) -> None:
        send_chat_action = getattr(self.bot, "send_chat_action", None)
        if send_chat_action is None:
            return
        result = send_chat_action(chat_id=chat_id, action="typing")
        if asyncio.iscoroutine(result):
            await result

    def _chat_allowed(self, chat_id: int) -> bool:
        if self.config.allowed_chat_ids:
            return chat_id in self.config.allowed_chat_ids
        return self.config.environment.lower() in {"local", "dev", "development", "test"}

    def _session(self, chat_id: int) -> TelegramTeamSession:
        session = self.registry.session(chat_id)
        session.sink.auto_sender = self._schedule_send
        return session

    def _schedule_send(self, message: TelegramOutgoingMessage) -> None:
        task = asyncio.create_task(self._send(message))
        self._send_tasks.add(task)
        task.add_done_callback(self._send_tasks.discard)

    def _schedule_log(
        self,
        *,
        text: str,
        job_id: str,
        run_id: str | None = None,
        intent: str | None = None,
        status: str | None = None,
        workspace: Path | None = None,
    ) -> None:
        if self.config.log_chat_id is None:
            return
        task = asyncio.create_task(
            self._send_log(
                text=text,
                job_id=job_id,
                run_id=run_id,
                intent=intent,
                status=status,
                workspace=workspace,
            )
        )
        self._send_tasks.add(task)
        task.add_done_callback(self._send_tasks.discard)

    async def _send_log(
        self,
        *,
        text: str,
        job_id: str,
        run_id: str | None = None,
        intent: str | None = None,
        status: str | None = None,
        workspace: Path | None = None,
    ) -> None:
        if self.config.log_chat_id is None:
            return
        details = [
            f"job_id={job_id}",
            f"run_id={run_id or 'pending'}",
        ]
        if intent:
            details.append(f"intent={intent}")
        if status:
            details.append(f"status={status}")
        details.append(f"workspace={workspace or 'pending'}")
        await self._send(
            TelegramOutgoingMessage(
                chat_id=self.config.log_chat_id,
                text=f"[Лог] {text} ({'; '.join(details)})",
                channel=TeamMessageChannel.LOG_CHAT,
            )
        )

    def _team_input(
        self,
        *,
        controller: TeamConversationController,
        text: str,
        attachments_count: int,
        attachments: tuple[TeamInputAttachment, ...],
    ) -> TeamInput:
        return TeamInput(
            text=text,
            attachments=attachments,
            attachments_count=attachments_count,
            active_run_id=next(iter(controller.state.active_runs), None),
            last_run_id=controller.state.last_run_id,
            failed_run_id=controller.state.last_failed_run_id,
            has_active_run=bool(controller.state.active_runs),
        )

    def _should_start_job(self, decision: TeamIntakeDecision) -> bool:
        if decision.intent == TeamInputIntent.FILE_TASK and not decision.should_start_run:
            return False
        return decision.intent in {
            TeamInputIntent.NEW_TASK,
            TeamInputIntent.FILE_TASK,
            TeamInputIntent.TASK_FOLLOWUP,
            TeamInputIntent.REVISE_PREVIOUS_RESULT,
        }

    def _start_background_job(
        self,
        *,
        chat_id: int,
        session_id: str,
        session: TelegramTeamSession,
        team_input: TeamInput,
        decision: TeamIntakeDecision,
    ) -> TeamRuntimeResponse:
        try:
            handle = self.jobs.start(
                session_id=session_id,
                user_task=team_input.text.strip(),
                runner=lambda: session.controller.handle(team_input),
            )
        except TeamJobAlreadyActiveError as exc:
            return TeamRuntimeResponse(
                user_visible_reply=(
                    "В этом чате задача уже выполняется. Дождись результата или вызови /stopall."
                ),
                decision=decision,
                status=TeamRuntimeStatus.RUNNING,
                run_id=exc.job_id,
                state=session.controller.state,
            )

        self._schedule_log(
            text="run_started",
            job_id=handle.job.id,
            intent=decision.intent.value,
            status=TeamJobStatus.RUNNING.value,
        )
        watch_task = asyncio.create_task(
            self._watch_job(chat_id=chat_id, handle=handle, intent=decision.intent.value)
        )
        self._watch_tasks.add(watch_task)
        watch_task.add_done_callback(self._watch_tasks.discard)
        return TeamRuntimeResponse(
            user_visible_reply="Принял задачу. Команда начала работу.",
            decision=decision,
            status=TeamRuntimeStatus.RUNNING,
            run_id=handle.job.id,
            state=session.controller.state,
        )

    async def _handle_status(
        self,
        *,
        session_id: str,
        controller: TeamConversationController,
        team_input: TeamInput,
        decision: TeamIntakeDecision,
    ) -> TeamRuntimeResponse:
        snapshot = self.jobs.snapshot(session_id)
        if snapshot is not None:
            return self._job_response(
                decision=decision,
                snapshot=snapshot,
                state=controller.state,
                text=self._status_text(snapshot),
            )
        return await controller.handle(team_input)

    async def _handle_stopall(
        self,
        *,
        session_id: str,
        controller: TeamConversationController,
        team_input: TeamInput,
        decision: TeamIntakeDecision,
    ) -> TeamRuntimeResponse:
        snapshot = await self.jobs.cancel_active(session_id, reason="stopall")
        await controller.handle(team_input)
        if snapshot is None:
            return TeamRuntimeResponse(
                user_visible_reply="Активных задач сейчас нет.",
                decision=decision,
                status=TeamRuntimeStatus.CANCELLED,
                state=controller.state,
            )
        self._schedule_log(
            text="run_cancelled",
            job_id=snapshot.job_id,
            run_id=snapshot.run_id,
            intent=decision.intent.value,
            status=snapshot.status.value,
            workspace=snapshot.workspace_path,
        )
        return self._job_response(
            decision=decision,
            snapshot=snapshot,
            state=controller.state,
            text="Остановил активную задачу.",
        )

    async def _watch_job(self, *, chat_id: int, handle: TeamJobHandle, intent: str) -> None:
        snapshot = await handle.wait()
        await self.drain_sends()
        if snapshot.status == TeamJobStatus.COMPLETED:
            await self._send_log(
                text="run_finished",
                job_id=snapshot.job_id,
                run_id=snapshot.run_id,
                intent=intent,
                status=snapshot.status.value,
                workspace=snapshot.workspace_path,
            )
            await self._send(
                TelegramOutgoingMessage(
                    chat_id=chat_id,
                    text=snapshot.final_text or "Готово.",
                    send_typing=True,
                )
            )
        elif snapshot.status == TeamJobStatus.FAILED:
            await self._send_log(
                text="run_failed",
                job_id=snapshot.job_id,
                run_id=snapshot.run_id,
                intent=intent,
                status=snapshot.status.value,
                workspace=snapshot.workspace_path,
            )
            await self._send(
                TelegramOutgoingMessage(
                    chat_id=chat_id,
                    text=self._failed_job_text(snapshot),
                    send_typing=True,
                )
            )

    def _job_response(
        self,
        *,
        decision: TeamIntakeDecision,
        snapshot: TeamJobSnapshot,
        state: Any,
        text: str,
    ) -> TeamRuntimeResponse:
        return TeamRuntimeResponse(
            user_visible_reply=text,
            decision=decision,
            status=self._runtime_status(snapshot.status),
            run_id=snapshot.run_id or snapshot.job_id,
            final_text=snapshot.final_text,
            workspace_path=snapshot.workspace_path,
            state=state,
        )

    def _runtime_status(self, status: TeamJobStatus) -> TeamRuntimeStatus:
        if status in {TeamJobStatus.PENDING, TeamJobStatus.RUNNING}:
            return TeamRuntimeStatus.RUNNING
        if status == TeamJobStatus.COMPLETED:
            return TeamRuntimeStatus.COMPLETED
        if status == TeamJobStatus.FAILED:
            return TeamRuntimeStatus.FAILED
        return TeamRuntimeStatus.CANCELLED

    def _status_text(self, snapshot: TeamJobSnapshot) -> str:
        run = snapshot.run_id or "ещё не создан"
        if snapshot.status in {TeamJobStatus.PENDING, TeamJobStatus.RUNNING}:
            lines = [
                "Активная задача: есть.",
                f"job_id: {snapshot.job_id}",
                f"status: {snapshot.status.value}",
                f"run_id: {run}",
            ]
            if snapshot.workspace_path:
                lines.append(f"workspace: {snapshot.workspace_path}")
            return "\n".join(lines)
        if snapshot.status == TeamJobStatus.COMPLETED:
            lines = [
                "Активная задача: нет.",
                f"Последняя завершённая задача: {snapshot.job_id}.",
                f"run_id: {run}",
            ]
            if snapshot.workspace_path:
                lines.append(f"workspace: {snapshot.workspace_path}")
            return "\n".join(lines)
        if snapshot.status == TeamJobStatus.FAILED:
            lines = [
                "Активная задача: нет.",
                f"Последняя failed задача: {snapshot.job_id}.",
                f"run_id: {run}",
            ]
            if snapshot.workspace_path:
                lines.append(f"workspace: {snapshot.workspace_path}")
            if snapshot.error_message:
                lines.append(f"error: {snapshot.error_message}")
            return "\n".join(lines)
        return "\n".join(
            [
                "Активная задача: нет.",
                f"Последняя задача отменена: {snapshot.job_id}.",
                f"run_id: {run}",
            ]
        )

    def _failed_job_text(self, snapshot: TeamJobSnapshot) -> str:
        lines = ["Команда завершилась с ошибкой."]
        if snapshot.run_id:
            lines.append(f"run_id: {snapshot.run_id}")
        if snapshot.workspace_path:
            lines.append(f"workspace: {snapshot.workspace_path}")
        if snapshot.error_message:
            lines.append(f"message: {snapshot.error_message}")
        if snapshot.run_id:
            lines.append(f"Можно продолжить: astra-nexus-team-resume {snapshot.run_id}")
        return "\n".join(lines)

    def _session_id(self, chat_id: int) -> str:
        return str(chat_id)

    def _response_text(self, response: TeamRuntimeResponse) -> str:
        lines = [response.user_visible_reply]
        if response.workspace_path is not None and response.status.value == "failed":
            lines.append("")
            lines.append(f"workspace: {response.workspace_path}")
        if response.status.value == "failed" and response.run_id is not None:
            lines.append(f"Можно продолжить: astra-nexus-team-resume {response.run_id}")
        return "\n".join(line for line in lines if line)

    async def _attachments_from_message(
        self,
        *,
        chat_id: int,
        message: Any,
    ) -> tuple[TeamInputAttachment, ...]:
        explicit = getattr(message, "team_attachments", None)
        if explicit is not None:
            return self.attachment_processor.process(tuple(explicit))

        document = getattr(message, "document", None)
        if document is not None:
            upload_path = await self._download_telegram_file(
                chat_id=chat_id,
                file_obj=document,
                filename=getattr(document, "file_name", None)
                or getattr(document, "file_id", "document"),
            )
            return self.attachment_processor.prepare_paths([upload_path], source="telegram")

        photo = self._largest_photo(message)
        if photo is None:
            return ()

        photo_id = getattr(photo, "file_unique_id", None) or getattr(photo, "file_id", "photo")
        upload_path = await self._download_telegram_file(
            chat_id=chat_id,
            file_obj=photo,
            filename=f"photo_{photo_id}.jpg",
        )
        return self.attachment_processor.prepare_paths([upload_path], source="telegram")

    def _largest_photo(self, message: Any) -> Any | None:
        photo = getattr(message, "photo", None)
        if not photo:
            return None
        if isinstance(photo, list | tuple):
            return photo[-1] if photo else None
        return photo

    async def _download_telegram_file(
        self,
        *,
        chat_id: int,
        file_obj: Any,
        filename: str,
    ) -> Path:
        file_size = getattr(file_obj, "file_size", None)
        if file_size is not None:
            self.attachment_processor._validate_size(filename, int(file_size))
        filename = Path(filename).name or "file"
        destination_dir = self.config.uploads_root / str(chat_id)
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / filename
        download = getattr(self.bot, "download", None)
        if download is None:
            raise RuntimeError("Telegram bot does not support file download")
        result = download(file_obj, destination=destination)
        if asyncio.iscoroutine(result):
            await result
        return destination


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
    await _wait_for_preview_job(bridge=bridge, chat_id=args.chat_id)

    for outgoing in bot.messages:
        label = "Лог" if outgoing.channel == TeamMessageChannel.LOG_CHAT else "Основной чат"
        print(f"[{label}] {outgoing.text}")
    return 0


def main_preview(argv: list[str] | None = None) -> int:
    return asyncio.run(run_preview(argv))


async def run_job_preview(argv: list[str] | None = None) -> int:
    args = _parse_job_preview_args(argv)
    messages = args.messages or [DEFAULT_TELEGRAM_PREVIEW_MESSAGE]
    settings = load_settings()
    config = TelegramTeamBridgeConfig(
        provider="fake",
        workspace_root=args.workspace_root or settings.team_runs_dir,
        log_chat_id=args.log_chat_id,
    )
    bot = RecordingTelegramBot()
    bridge = TelegramTeamBridge(bot=bot, config=config)
    printed_count = 0

    for message in messages:
        print(f"> {message}")
        await bridge.handle_text(chat_id=args.chat_id, text=message)
        await asyncio.sleep(0)
        await bridge.drain_sends()
        printed_count = _print_new_messages(bot.messages, printed_count)

    await _wait_for_preview_job(bridge=bridge, chat_id=args.chat_id)
    _print_new_messages(bot.messages, printed_count)
    return 0


def main_job_preview(argv: list[str] | None = None) -> int:
    return asyncio.run(run_job_preview(argv))


async def run_live_preview(argv: list[str] | None = None) -> int:
    args = _parse_live_preview_args(argv)
    settings = load_settings()
    config = TelegramTeamBridgeConfig(
        provider="fake",
        workspace_root=args.workspace_root or settings.team_runs_dir,
        log_chat_id=args.log_chat_id,
        send_typing=args.send_typing,
    )
    bot = RecordingTelegramBot()
    provider = _LivePreviewProvider()
    bridge = TelegramTeamBridge(
        bot=bot,
        config=config,
        provider_factory=lambda: provider,
    )
    printed_count = 0

    with tempfile.TemporaryDirectory(prefix="astra-nexus-telegram-live-preview-") as temp_dir:
        temp_path = Path(temp_dir)
        file_without_caption = temp_path / "incoming-context.md"
        file_without_caption.write_text("Контекст без подписи.", encoding="utf-8")
        file_with_caption = temp_path / "task-brief.md"
        file_with_caption.write_text("Контекст для задачи из файла.", encoding="utf-8")

        scenarios: tuple[tuple[str, str, tuple[TeamInputAttachment, ...]], ...] = (
            ("брат че думаешь", "брат че думаешь", ()),
            ("сделай краткий план AI-команды", "сделай краткий план AI-команды", ()),
            ("/status", "/status", ()),
            ("/stopall", "/stopall", ()),
            (
                "file without caption",
                "",
                bridge.attachment_processor.prepare_paths(
                    [file_without_caption],
                    source="telegram_preview",
                ),
            ),
            (
                "file with caption",
                "проверь файл и сделай краткий вывод",
                bridge.attachment_processor.prepare_paths(
                    [file_with_caption],
                    source="telegram_preview",
                ),
            ),
        )

        for label, text, attachments in scenarios:
            print(f"> {label}")
            await bridge.handle_text(chat_id=args.chat_id, text=text, attachments=attachments)
            if label == "/stopall":
                provider.release.set()
            await asyncio.sleep(0)
            await bridge.drain_sends()
            printed_count = _print_new_messages(bot.messages, printed_count)

        await _wait_for_preview_job(bridge=bridge, chat_id=args.chat_id)
        _print_new_messages(bot.messages, printed_count)
    return 0


def main_live_preview(argv: list[str] | None = None) -> int:
    return asyncio.run(run_live_preview(argv))


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

    if args.dry_run and not token:
        print(
            "Astra Nexus AI Team Telegram bot dry-run: "
            f"provider={config.provider}, workspace_root={config.workspace_root}. "
            "Telegram token is not required for dry-run."
        )
        return 0

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


def _parse_job_preview_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview Telegram AI Team background job flow without Telegram."
    )
    parser.add_argument("messages", nargs="*", help="Последовательность сообщений пользователя.")
    parser.add_argument("--chat-id", type=int, default=100)
    parser.add_argument("--log-chat-id", type=int, default=None)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Папка для team run workspaces.",
    )
    return parser.parse_args(argv)


def _parse_live_preview_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview live Telegram AI Team runtime without Telegram API."
    )
    parser.add_argument("--chat-id", type=int, default=100)
    parser.add_argument("--log-chat-id", type=int, default=200)
    parser.add_argument("--send-typing", action=argparse.BooleanOptionalAction, default=True)
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


async def _wait_for_preview_job(*, bridge: TelegramTeamBridge, chat_id: int) -> None:
    session_id = bridge._session_id(chat_id)
    if bridge.jobs.active(session_id) is not None:
        await bridge.jobs.wait(session_id)
    await bridge.drain_watchers()
    await asyncio.sleep(0)
    await bridge.drain_sends()


def _print_new_messages(messages: list[TelegramOutgoingMessage], printed_count: int) -> int:
    for outgoing in messages[printed_count:]:
        label = "Лог" if outgoing.channel == TeamMessageChannel.LOG_CHAT else "Основной чат"
        print(f"[{label}] {outgoing.text}")
    return len(messages)


if __name__ == "__main__":
    raise SystemExit(main_preview())
