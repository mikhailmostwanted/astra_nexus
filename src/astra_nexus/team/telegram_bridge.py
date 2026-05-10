from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import random
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from astra_nexus.config.settings import Settings, load_settings
from astra_nexus.team.atmosphere import (
    AtmosphereProfile,
    AtmosphereTeamMessageSink,
    TeamAtmosphereRenderer,
)
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
from astra_nexus.team.models import AgentRole
from astra_nexus.team.provider import TeamProvider
from astra_nexus.team.run_registry import TeamRunRegistry, TeamRunRegistryEntry
from astra_nexus.team.runtime import (
    TeamConversationController,
    TeamRuntimeResponse,
    TeamRuntimeStatus,
)
from astra_nexus.team.telegram_render import (
    TELEGRAM_HTML_PARSE_MODE,
    TELEGRAM_INTERNAL_CHUNK_LIMIT,
    TelegramRenderedChunk,
    render_answer_for_telegram,
    split_plain_text,
    split_telegram_html_blocks,
    strip_telegram_html,
)
from astra_nexus.team.workspace import TeamRunWorkspace
from astra_nexus.utils.logging import configure_logging

DEFAULT_TELEGRAM_PREVIEW_MESSAGE = "брат че думаешь"
DEFAULT_PROVIDER = "fake"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramTeamBridgeConfig:
    provider: str = DEFAULT_PROVIDER
    workspace_root: Path = Path("data/team_runs")
    uploads_root: Path = Path("data/team_telegram_downloads")
    attachment_max_files: int = 5
    attachment_max_bytes: int = 10 * 1024 * 1024
    attachment_text_max_chars: int = 20000
    attachment_max_extracted_chars: int = 50000
    attachment_max_prompt_chars: int = 20000
    attachment_pdf_max_pages: int = 30
    attachment_docx_include_tables: bool = True
    log_chat_id: int | None = None
    allowed_chat_ids: tuple[int, ...] = ()
    environment: str = "local"
    send_typing: bool = True
    human_messages: bool = True
    atmosphere: AtmosphereProfile = field(default_factory=AtmosphereProfile)
    atmosphere_mode: str = "template"
    atmosphere_snippet_max_chars: int = 220
    send_internal_artifacts: bool = False
    send_requested_files: bool = True

    @classmethod
    def from_settings(cls, settings: Settings) -> TelegramTeamBridgeConfig:
        max_file_size_mb = max(1, settings.team_telegram_max_file_size_mb)
        atmosphere_mode = settings.team_atmosphere_mode
        if settings.team_telegram_provider == "nodriver" and atmosphere_mode == "template":
            atmosphere_mode = "minimal"
        return cls(
            provider=settings.team_telegram_provider,
            workspace_root=settings.team_runs_dir,
            uploads_root=settings.team_telegram_downloads_dir,
            attachment_max_files=settings.team_attachments_max_files,
            attachment_max_bytes=max_file_size_mb * 1024 * 1024,
            attachment_text_max_chars=settings.team_attachment_text_max_chars,
            attachment_max_extracted_chars=settings.team_attachment_max_extracted_chars,
            attachment_max_prompt_chars=settings.team_attachment_max_prompt_chars,
            attachment_pdf_max_pages=settings.team_attachment_pdf_max_pages,
            attachment_docx_include_tables=settings.team_attachment_docx_include_tables,
            log_chat_id=settings.team_telegram_log_chat_id,
            allowed_chat_ids=_parse_allowed_chat_ids(settings.team_telegram_allowed_chat_ids),
            environment=settings.environment,
            send_typing=settings.team_telegram_send_typing,
            human_messages=settings.team_telegram_human_messages,
            atmosphere=AtmosphereProfile.from_settings(settings),
            atmosphere_mode=atmosphere_mode,
            atmosphere_snippet_max_chars=settings.team_atmosphere_snippet_max_chars,
            send_internal_artifacts=settings.team_telegram_send_internal_artifacts,
            send_requested_files=settings.team_telegram_send_requested_files,
        )


@dataclass(frozen=True)
class TelegramOutgoingMessage:
    chat_id: int
    text: str
    channel: TeamMessageChannel = TeamMessageChannel.MAIN_CHAT
    send_typing: bool = False
    parse_mode: str | None = None


@dataclass(frozen=True)
class TelegramOutgoingDocument:
    chat_id: int
    path: Path
    filename: str
    caption: str | None = None


@dataclass(frozen=True)
class TelegramChatAction:
    chat_id: int
    action: str


class RecordingTelegramBot:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.messages: list[TelegramOutgoingMessage] = []
        self.documents: list[TelegramOutgoingDocument] = []
        self.chat_actions: list[TelegramChatAction] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> TelegramOutgoingMessage:
        message = TelegramOutgoingMessage(
            chat_id=chat_id,
            text=text,
            channel=kwargs.get("channel", TeamMessageChannel.MAIN_CHAT),
            send_typing=kwargs.get("send_typing", False),
            parse_mode=kwargs.get("parse_mode"),
        )
        self.messages.append(message)
        return message

    async def send_chat_action(self, chat_id: int, action: str, **kwargs: Any) -> None:
        self.chat_actions.append(TelegramChatAction(chat_id=chat_id, action=action))

    async def send_document(
        self,
        chat_id: int,
        document: Any,
        **kwargs: Any,
    ) -> TelegramOutgoingDocument:
        path = _document_path(document)
        filename = kwargs.get("filename") or kwargs.get("file_name") or path.name
        outgoing = TelegramOutgoingDocument(
            chat_id=chat_id,
            path=path,
            filename=filename,
            caption=kwargs.get("caption"),
        )
        self.documents.append(outgoing)
        return outgoing


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
        session_id: str | None = None,
        provider: str | None = None,
        execution_mode: str | None = None,
        atmosphere_mode: str = "template",
    ) -> None:
        self.chat_id = chat_id
        self.log_chat_id = log_chat_id
        self.human_messages = human_messages
        self.session_id = session_id
        self.provider = provider
        self.execution_mode = execution_mode
        self.atmosphere_mode = atmosphere_mode
        self.current_job_id: str | None = None
        self.current_intent: str | None = None
        self.outbox: list[TelegramOutgoingMessage] = []
        self.auto_sender: Callable[[TelegramOutgoingMessage], None] | None = None
        self.job_update_callback: Callable[[TelegramTeamMessageSink, TeamMessage], None] | None = (
            None
        )
        self._main_dedupe_keys: set[tuple[str, str, str, str, str]] = set()

    def publish(self, message: TeamMessage) -> None:
        if self.job_update_callback is not None:
            try:
                self.job_update_callback(self, message)
            except Exception:
                logger.warning("Could not update Telegram team job state", exc_info=True)
        if message.channel in {TeamMessageChannel.LOG_CHAT, TeamMessageChannel.DEBUG}:
            if self.log_chat_id is None:
                return
        elif message.channel == TeamMessageChannel.MAIN_CHAT and not self.human_messages:
            return
        elif (
            message.channel == TeamMessageChannel.MAIN_CHAT
            and self.atmosphere_mode in {"minimal", "result_snippet", "off"}
            and message.metadata.get("atmosphere")
        ):
            return
        if message.channel == TeamMessageChannel.MAIN_CHAT and self._is_duplicate_main(message):
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

    def publish_human_text(
        self,
        *,
        run_id: str,
        agent_role: AgentRole | str | None,
        phase: str,
        text: str,
    ) -> None:
        normalized = _normalize_dedupe_text(text)
        if not normalized:
            return
        role_text = agent_role.value if isinstance(agent_role, AgentRole) else str(agent_role or "")
        key = (self.session_id or "", run_id, role_text, phase, normalized)
        if key in self._main_dedupe_keys:
            return
        self._main_dedupe_keys.add(key)
        outgoing = TelegramOutgoingMessage(
            chat_id=self.chat_id,
            text=normalized,
            channel=TeamMessageChannel.MAIN_CHAT,
            send_typing=True,
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
        for key, value in (
            ("event_type", message.metadata.get("event_type")),
            ("run_id", message.metadata.get("run_id") or message.run_id),
            ("job_id", message.metadata.get("job_id") or self.current_job_id),
            ("session_id", message.metadata.get("session_id") or self.session_id),
            ("intent", message.metadata.get("intent") or self.current_intent),
            ("provider", message.metadata.get("provider") or self.provider),
            ("execution_mode", message.metadata.get("execution_mode") or self.execution_mode),
            ("workspace", message.metadata.get("workspace")),
            ("status", message.metadata.get("status")),
        ):
            if value:
                details.append(f"{key}={value}")
        suffix = f" ({'; '.join(details)})" if details else ""
        return f"[Лог] {message.text}{suffix}"

    def set_job_context(self, *, job_id: str | None, intent: str | None) -> None:
        self.current_job_id = job_id
        self.current_intent = intent

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

    def _is_duplicate_main(self, message: TeamMessage) -> bool:
        role_text = message.author_role.value if message.author_role is not None else ""
        phase = str(message.metadata.get("phase") or message.type.value)
        key = (
            self.session_id or "",
            message.run_id,
            role_text,
            phase,
            _normalize_dedupe_text(self.render(message)),
        )
        if key in self._main_dedupe_keys:
            return True
        self._main_dedupe_keys.add(key)
        return False


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
                session_id=str(chat_id),
                provider=self.config.provider,
                execution_mode="sequential",
                atmosphere_mode=self.config.atmosphere_mode,
            )
            self.sessions[chat_id] = TelegramTeamSession(
                controller=TeamConversationController(
                    provider=self.provider_factory(),
                    workspace=TeamRunWorkspace(root_path=self.config.workspace_root),
                    message_sink=AtmosphereTeamMessageSink(
                        sink,
                        renderer=TeamAtmosphereRenderer(self.config.atmosphere),
                    ),
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
        run_registry: TeamRunRegistry | None = None,
        jobs: TeamJobManager | None = None,
    ) -> None:
        self.bot = bot
        self.config = config or TelegramTeamBridgeConfig()
        self.provider_factory = provider_factory or _provider_factory(self.config.provider)
        self.registry = registry or TelegramTeamSessionRegistry(
            config=self.config,
            provider_factory=self.provider_factory,
        )
        self.run_registry = run_registry or TeamRunRegistry(self.config.workspace_root)
        self.jobs = jobs or TeamJobManager()
        self.attachment_processor = TeamAttachmentProcessor(
            max_files=self.config.attachment_max_files,
            max_bytes=self.config.attachment_max_bytes,
            max_extracted_chars=self.config.attachment_max_extracted_chars,
            max_prompt_chars=self.config.attachment_max_prompt_chars,
            pdf_max_pages=self.config.attachment_pdf_max_pages,
            docx_include_tables=self.config.attachment_docx_include_tables,
        )
        self._send_tasks: set[asyncio.Task[None]] = set()
        self._watch_tasks: set[asyncio.Task[None]] = set()
        self._migrated_chat_ids: dict[int, int] = {}

    async def handle_message(self, message: Any) -> TeamRuntimeResponse | None:
        chat_id = self._resolve_migrated_chat_id(int(message.chat.id))
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
        chat_id = self._resolve_migrated_chat_id(int(chat_id))
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

        if decision.intent == TeamInputIntent.HELP_REQUEST:
            response = self._handle_help(decision=decision, state=controller.state)
            await self._send(
                TelegramOutgoingMessage(chat_id=chat_id, text=response.user_visible_reply)
            )
            return response

        if decision.intent == TeamInputIntent.HEALTH_REQUEST:
            response = self._handle_health(
                session_id=session_id,
                decision=decision,
                state=controller.state,
            )
            await self._send(
                TelegramOutgoingMessage(chat_id=chat_id, text=response.user_visible_reply)
            )
            return response

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

        if decision.intent == TeamInputIntent.RUNS_REQUEST:
            response = self._handle_runs(
                session_id=session_id,
                decision=decision,
                state=controller.state,
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
        message = replace(message, chat_id=self._resolve_migrated_chat_id(message.chat_id))
        chunks = self._telegram_message_chunks(message)
        for index, chunk in enumerate(chunks):
            await self._send_one(
                replace(
                    message,
                    text=chunk.text,
                    parse_mode=chunk.parse_mode,
                    send_typing=message.send_typing and index == 0,
                )
            )

    async def _send_one(self, message: TelegramOutgoingMessage) -> None:
        if message.send_typing and self.config.send_typing:
            await self._send_typing(message.chat_id)
        if message.channel == TeamMessageChannel.MAIN_CHAT and self.config.atmosphere.send_delays:
            await asyncio.sleep(
                random.uniform(
                    self.config.atmosphere.min_delay_seconds,
                    self.config.atmosphere.max_delay_seconds,
                )
            )
        if isinstance(self.bot, RecordingTelegramBot):
            await self.bot.send_message(
                chat_id=message.chat_id,
                text=message.text,
                channel=message.channel,
                send_typing=message.send_typing,
                parse_mode=message.parse_mode,
            )
            return
        try:
            await self._send_with_migration_retry(
                chat_id=message.chat_id,
                operation="send_message",
                sender=lambda target_chat_id: self.bot.send_message(
                    chat_id=target_chat_id,
                    text=message.text,
                    parse_mode=message.parse_mode,
                ),
            )
        except Exception:
            if message.parse_mode != TELEGRAM_HTML_PARSE_MODE:
                raise
            fallback = strip_telegram_html(message.text)
            logger.warning("Telegram HTML send failed; falling back to plain text", exc_info=True)
            await self._send_with_migration_retry(
                chat_id=message.chat_id,
                operation="send_message",
                sender=lambda target_chat_id: self.bot.send_message(
                    chat_id=target_chat_id,
                    text=fallback,
                ),
            )

    def _telegram_message_chunks(
        self,
        message: TelegramOutgoingMessage,
    ) -> tuple[TelegramRenderedChunk, ...]:
        if message.parse_mode == TELEGRAM_HTML_PARSE_MODE:
            chunks = split_telegram_html_blocks(
                message.text.split("\n\n"),
                chunk_limit=TELEGRAM_INTERNAL_CHUNK_LIMIT,
            )
            return tuple(
                TelegramRenderedChunk(text=chunk, parse_mode=TELEGRAM_HTML_PARSE_MODE)
                for chunk in chunks
            )
        chunks = split_plain_text(message.text, chunk_limit=TELEGRAM_INTERNAL_CHUNK_LIMIT)
        return tuple(
            TelegramRenderedChunk(text=chunk, parse_mode=message.parse_mode) for chunk in chunks
        )

    async def _send_document(
        self,
        *,
        chat_id: int,
        path: Path,
        caption: str | None = None,
    ) -> None:
        chat_id = self._resolve_migrated_chat_id(chat_id)
        if not path.exists() or not path.is_file():
            return
        send_document = getattr(self.bot, "send_document", None)
        if send_document is None:
            return
        if isinstance(self.bot, RecordingTelegramBot):
            await self.bot.send_document(
                chat_id=chat_id,
                document=path,
                filename=path.name,
                caption=caption,
            )
            return

        document: Any = path
        try:
            from aiogram.types import FSInputFile

            document = FSInputFile(path)
        except Exception:
            document = path

        await self._send_with_migration_retry(
            chat_id=chat_id,
            operation="send_document",
            sender=lambda target_chat_id: send_document(
                chat_id=target_chat_id,
                document=document,
                caption=caption,
            ),
        )

    async def _send_typing(self, chat_id: int) -> None:
        chat_id = self._resolve_migrated_chat_id(chat_id)
        send_chat_action = getattr(self.bot, "send_chat_action", None)
        if send_chat_action is None:
            return
        await self._send_with_migration_retry(
            chat_id=chat_id,
            operation="send_chat_action",
            sender=lambda target_chat_id: send_chat_action(
                chat_id=target_chat_id,
                action="typing",
            ),
        )

    async def _send_with_migration_retry(
        self,
        *,
        chat_id: int,
        operation: str,
        sender: Callable[[int], Any],
    ) -> Any:
        target_chat_id = self._resolve_migrated_chat_id(chat_id)
        try:
            return await self._await_if_needed(sender(target_chat_id))
        except Exception as exc:
            new_chat_id = self._migrate_to_chat_id(exc)
            if new_chat_id is None:
                raise

        self._record_chat_migration(
            old_chat_id=target_chat_id,
            new_chat_id=new_chat_id,
            operation=operation,
        )
        try:
            return await self._await_if_needed(sender(new_chat_id))
        except Exception as exc:
            retry_chat_id = self._migrate_to_chat_id(exc)
            if retry_chat_id is None:
                raise
            self._record_chat_migration(
                old_chat_id=new_chat_id,
                new_chat_id=retry_chat_id,
                operation=operation,
            )
            logger.warning(
                "Telegram chat migrated again during %s after one retry: %s -> %s",
                operation,
                new_chat_id,
                retry_chat_id,
            )
            return None

    async def _await_if_needed(self, result: Any) -> Any:
        if asyncio.iscoroutine(result):
            return await result
        return result

    def _migrate_to_chat_id(self, exc: Exception) -> int | None:
        migrate_to_chat_id = getattr(exc, "migrate_to_chat_id", None)
        if migrate_to_chat_id is None:
            return None
        try:
            return int(migrate_to_chat_id)
        except (TypeError, ValueError):
            return None

    def _record_chat_migration(
        self,
        *,
        old_chat_id: int,
        new_chat_id: int,
        operation: str,
    ) -> None:
        old_chat_id = int(old_chat_id)
        new_chat_id = int(new_chat_id)
        if old_chat_id == new_chat_id:
            return
        new_chat_id = self._resolve_migrated_chat_id(new_chat_id)
        self._migrated_chat_ids[old_chat_id] = new_chat_id
        for source_chat_id, target_chat_id in tuple(self._migrated_chat_ids.items()):
            if target_chat_id == old_chat_id:
                self._migrated_chat_ids[source_chat_id] = new_chat_id

        allowed_chat_ids = self.config.allowed_chat_ids
        if (
            allowed_chat_ids
            and old_chat_id in allowed_chat_ids
            and new_chat_id not in allowed_chat_ids
        ):
            allowed_chat_ids = (*allowed_chat_ids, new_chat_id)

        log_chat_id = self.config.log_chat_id
        if log_chat_id == old_chat_id:
            log_chat_id = new_chat_id

        if (
            allowed_chat_ids != self.config.allowed_chat_ids
            or log_chat_id != self.config.log_chat_id
        ):
            self.config = replace(
                self.config,
                allowed_chat_ids=allowed_chat_ids,
                log_chat_id=log_chat_id,
            )
            self.registry.config = self.config

        self._migrate_runtime_chat_state(old_chat_id=old_chat_id, new_chat_id=new_chat_id)
        logger.warning(
            "Telegram chat migrated during %s: %s -> %s",
            operation,
            old_chat_id,
            new_chat_id,
        )

    def _migrate_runtime_chat_state(self, *, old_chat_id: int, new_chat_id: int) -> None:
        session = self.registry.sessions.pop(old_chat_id, None)
        if session is not None:
            session.sink.chat_id = new_chat_id
            if session.sink.session_id == str(old_chat_id):
                session.sink.session_id = str(new_chat_id)
            self.registry.sessions.setdefault(new_chat_id, session)

        for existing_session in self.registry.sessions.values():
            if existing_session.sink.chat_id == old_chat_id:
                existing_session.sink.chat_id = new_chat_id
            if existing_session.sink.log_chat_id == old_chat_id:
                existing_session.sink.log_chat_id = new_chat_id
            if existing_session.sink.session_id == str(old_chat_id):
                existing_session.sink.session_id = str(new_chat_id)

        self._migrate_job_session_ids(
            old_session_id=str(old_chat_id), new_session_id=str(new_chat_id)
        )

    def _migrate_job_session_ids(self, *, old_session_id: str, new_session_id: str) -> None:
        job_maps = (
            self.jobs.active_jobs,
            self.jobs.last_jobs,
            self.jobs.last_completed_jobs,
            self.jobs.last_failed_jobs,
            self.jobs.last_cancelled_jobs,
        )
        for job_map in job_maps:
            job = job_map.pop(old_session_id, None)
            if job is None:
                continue
            job.session_id = new_session_id
            job_map.setdefault(new_session_id, job)

    def _resolve_migrated_chat_id(self, chat_id: int) -> int:
        resolved_chat_id = int(chat_id)
        seen: set[int] = set()
        while resolved_chat_id in self._migrated_chat_ids and resolved_chat_id not in seen:
            seen.add(resolved_chat_id)
            resolved_chat_id = self._migrated_chat_ids[resolved_chat_id]
        return resolved_chat_id

    def _chat_allowed(self, chat_id: int) -> bool:
        chat_id = self._resolve_migrated_chat_id(chat_id)
        if self.config.allowed_chat_ids:
            return chat_id in self.config.allowed_chat_ids
        return self.config.environment.lower() in {"local", "dev", "development", "test"}

    def _session(self, chat_id: int) -> TelegramTeamSession:
        session = self.registry.session(chat_id)
        session.sink.auto_sender = self._schedule_send
        session.sink.job_update_callback = self._update_active_job_from_message
        return session

    def _update_active_job_from_message(
        self,
        sink: TelegramTeamMessageSink,
        message: TeamMessage,
    ) -> None:
        metadata = message.metadata
        run_id = str(metadata.get("run_id") or message.run_id or "")
        if not run_id:
            return
        session_id = str(metadata.get("session_id") or sink.session_id or sink.chat_id)
        workspace_path = self._active_job_workspace_path(run_id=run_id, metadata=metadata)
        event_type = str(metadata.get("event_type") or message.type.value)
        current_agent = self._message_agent_role(message)
        current_stage = str(metadata.get("execution_step_id") or event_type)
        self.jobs.update_active(
            session_id,
            run_id=run_id,
            workspace_path=workspace_path,
            current_agent=current_agent,
            current_stage=current_stage,
        )

    def _active_job_workspace_path(
        self,
        *,
        run_id: str,
        metadata: dict[str, Any],
    ) -> Path | None:
        workspace = metadata.get("workspace")
        if workspace and str(workspace).lower() not in {"none", "null"}:
            return Path(str(workspace))
        if run_id.startswith("team_run_"):
            return self.config.workspace_root / run_id
        return None

    def _message_agent_role(self, message: TeamMessage) -> str | None:
        if message.author_role is not None:
            return message.author_role.value
        role = message.metadata.get("agent_role") or message.metadata.get("role")
        return str(role) if role else None

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
        session_id: str | None = None,
        provider: str | None = None,
        execution_mode: str | None = None,
        error_message: str | None = None,
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
                session_id=session_id,
                provider=provider,
                execution_mode=execution_mode,
                error_message=error_message,
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
        session_id: str | None = None,
        provider: str | None = None,
        execution_mode: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if self.config.log_chat_id is None:
            return
        details = [
            f"job_id={job_id}",
            f"run_id={run_id or 'pending'}",
            f"session_id={session_id or 'unknown'}",
        ]
        if intent:
            details.append(f"intent={intent}")
        if provider:
            details.append(f"provider={provider}")
        if execution_mode:
            details.append(f"execution_mode={execution_mode}")
        if status:
            details.append(f"status={status}")
        if error_message:
            details.append(f"error={error_message}")
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
        job_context: dict[str, str] = {}

        async def run_job() -> TeamRuntimeResponse:
            metadata = {
                "session_id": session_id,
                "chat_id": str(chat_id),
                "job_id": job_context.get("job_id"),
                "provider": self.config.provider,
                "execution_mode": "sequential",
                "intent": decision.intent.value,
            }
            return await session.controller.handle(
                replace(
                    team_input,
                    metadata={**team_input.metadata, **metadata},
                )
            )

        try:
            handle = self.jobs.start(
                session_id=session_id,
                user_task=team_input.text.strip(),
                runner=run_job,
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

        job_context["job_id"] = handle.job.id
        session.sink.set_job_context(job_id=handle.job.id, intent=decision.intent.value)
        self._schedule_log(
            text="run_started",
            job_id=handle.job.id,
            intent=decision.intent.value,
            status=TeamJobStatus.RUNNING.value,
            session_id=session_id,
            provider=self.config.provider,
            execution_mode="sequential",
        )
        watch_task = asyncio.create_task(
            self._watch_job(chat_id=chat_id, handle=handle, intent=decision.intent.value)
        )
        self._watch_tasks.add(watch_task)
        watch_task.add_done_callback(self._watch_tasks.discard)
        return TeamRuntimeResponse(
            user_visible_reply=self._task_started_reply(),
            decision=decision,
            status=TeamRuntimeStatus.RUNNING,
            run_id=handle.job.id,
            state=session.controller.state,
        )

    def _handle_help(self, *, decision: TeamIntakeDecision, state: Any) -> TeamRuntimeResponse:
        return TeamRuntimeResponse(
            user_visible_reply=self._help_text(),
            decision=decision,
            status=TeamRuntimeStatus.IDLE,
            state=state,
        )

    def _handle_health(
        self,
        *,
        session_id: str,
        decision: TeamIntakeDecision,
        state: Any,
    ) -> TeamRuntimeResponse:
        return TeamRuntimeResponse(
            user_visible_reply=self._health_text(session_id=session_id),
            decision=decision,
            status=TeamRuntimeStatus.IDLE,
            state=state,
        )

    async def _handle_status(
        self,
        *,
        session_id: str,
        controller: TeamConversationController,
        team_input: TeamInput,
        decision: TeamIntakeDecision,
    ) -> TeamRuntimeResponse:
        snapshot = self.jobs.active(session_id)
        if snapshot is not None:
            return self._job_response(
                decision=decision,
                snapshot=snapshot,
                state=controller.state,
                text=self._status_text(snapshot),
            )
        registry_entry = self.run_registry.last_terminal_run(session_id=session_id)
        if registry_entry is not None:
            return TeamRuntimeResponse(
                user_visible_reply=self._registry_status_text(registry_entry),
                decision=decision,
                status=self._runtime_status_from_registry(registry_entry.status),
                run_id=registry_entry.run_id,
                final_text=registry_entry.final_result,
                workspace_path=registry_entry.workspace_path,
                state=controller.state,
            )
        return await controller.handle(team_input)

    def _handle_runs(
        self,
        *,
        session_id: str,
        decision: TeamIntakeDecision,
        state: Any,
    ) -> TeamRuntimeResponse:
        entries = self.run_registry.latest_runs(session_id=session_id, limit=5)
        text = self._runs_text(entries)
        return TeamRuntimeResponse(
            user_visible_reply=text,
            decision=decision,
            status=TeamRuntimeStatus.IDLE,
            run_id=entries[0].run_id if entries else None,
            workspace_path=entries[0].workspace_path if entries else None,
            state=state,
        )

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
                user_visible_reply=TeamAtmosphereRenderer(self.config.atmosphere).stopall_reply(
                    had_active_task=False
                ),
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
            session_id=session_id,
            provider=self.config.provider,
            execution_mode="sequential",
        )
        return self._job_response(
            decision=decision,
            snapshot=snapshot,
            state=controller.state,
            text=TeamAtmosphereRenderer(self.config.atmosphere).stopall_reply(had_active_task=True),
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
                session_id=snapshot.session_id,
                provider=self.config.provider,
                execution_mode="sequential",
            )
            if self.config.atmosphere_mode == "result_snippet":
                await self._send_result_snippets(chat_id=chat_id, snapshot=snapshot)
            if self._output_requested_as_file(snapshot):
                await self._send_completed_artifacts(chat_id=chat_id, snapshot=snapshot)
            else:
                final_render = self._completed_final_render(snapshot)
                for chunk in final_render.chunks:
                    await self._send(
                        TelegramOutgoingMessage(
                            chat_id=chat_id,
                            text=chunk.text,
                            send_typing=True,
                            parse_mode=chunk.parse_mode,
                        )
                    )
                await self._send_completed_artifacts(chat_id=chat_id, snapshot=snapshot)
        elif snapshot.status == TeamJobStatus.FAILED:
            technical_error = self._technical_error_text(snapshot)
            await self._send_log(
                text="run_failed",
                job_id=snapshot.job_id,
                run_id=snapshot.run_id,
                intent=intent,
                status=snapshot.status.value,
                workspace=snapshot.workspace_path,
                session_id=snapshot.session_id,
                provider=self.config.provider,
                execution_mode="sequential",
                error_message=technical_error or snapshot.error_message,
            )
            await self._send(
                TelegramOutgoingMessage(
                    chat_id=chat_id,
                    text=self._failed_job_text(snapshot),
                    send_typing=True,
                )
            )
        elif snapshot.status == TeamJobStatus.CANCELLED:
            await self._send_log(
                text="run_cancelled",
                job_id=snapshot.job_id,
                run_id=snapshot.run_id,
                intent=intent,
                status=snapshot.status.value,
                workspace=snapshot.workspace_path,
                session_id=snapshot.session_id,
                provider=self.config.provider,
                execution_mode="sequential",
                error_message=snapshot.error_message,
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

    def _runtime_status_from_registry(self, status: str) -> TeamRuntimeStatus:
        if status == "completed":
            return TeamRuntimeStatus.COMPLETED
        if status == "failed":
            return TeamRuntimeStatus.FAILED
        if status == "cancelled":
            return TeamRuntimeStatus.CANCELLED
        if status == "running":
            return TeamRuntimeStatus.RUNNING
        return TeamRuntimeStatus.IDLE

    def _status_text(self, snapshot: TeamJobSnapshot) -> str:
        run = snapshot.run_id or "ещё не создан"
        workspace = snapshot.workspace_path or "нет"
        if snapshot.status in {TeamJobStatus.PENDING, TeamJobStatus.RUNNING}:
            lines = [
                "Активная задача: есть.",
                "Кто работает: команда.",
                f"job_id: {snapshot.job_id}",
                f"run_id: {run}",
                f"provider: {self.config.provider}",
                f"started_at: {_datetime_text(snapshot.started_at)}",
                f"status: {snapshot.status.value}",
                f"workspace: {workspace}",
            ]
            if snapshot.current_agent:
                lines.append(f"current_agent: {snapshot.current_agent}")
            if snapshot.current_stage:
                lines.append(f"current_stage: {snapshot.current_stage}")
            lines.append("Последний результат: пока нет.")
            return "\n".join(lines)
        if snapshot.status == TeamJobStatus.COMPLETED:
            return "\n".join(
                [
                    "Активная задача: нет.",
                    "Кто работает: никто.",
                    f"Последняя завершённая задача: {snapshot.job_id}.",
                    f"run_id: {run}",
                    f"workspace: {workspace}",
                    "Последний результат: "
                    f"{_preview_text(_clean_final_answer_text(snapshot.final_text or ''))}",
                ]
            )
        if snapshot.status == TeamJobStatus.FAILED:
            lines = [
                "Активная задача: нет.",
                "Кто работает: никто.",
                f"job_id: {snapshot.job_id}",
                f"status: {snapshot.status.value}",
                f"run_id: {run}",
                f"workspace: {workspace}",
                "Последний результат: задача завершилась с ошибкой.",
            ]
            if snapshot.error_message:
                lines.append(f"error: {snapshot.error_message}")
            return "\n".join(lines)
        return "\n".join(
            [
                "Активная задача: нет.",
                "Кто работает: никто.",
                f"Последняя задача отменена: {snapshot.job_id}.",
                f"run_id: {run}",
                f"workspace: {workspace}",
                "Последний результат: задача отменена.",
            ]
        )

    def _failed_job_text(self, snapshot: TeamJobSnapshot) -> str:
        lines = ["Команда завершилась с ошибкой."]
        if snapshot.run_id:
            lines.append(f"run_id: {snapshot.run_id}")
        if snapshot.workspace_path:
            lines.append(f"workspace: {snapshot.workspace_path}")
        payload = self._workspace_run_payload(snapshot.workspace_path)
        artifacts_count = _int_or_zero(payload.get("artifacts_count")) if payload else 0
        primary_artifact = _path_or_none(payload.get("primary_artifact_path")) if payload else None
        if artifacts_count:
            lines.append(f"artifacts: {artifacts_count}")
            if primary_artifact is not None:
                lines.append(f"primary_artifact: {primary_artifact}")
        if snapshot.error_message:
            lines.append("message: детали ошибки отправлены в log chat.")
        if snapshot.run_id:
            lines.append(f"Можно продолжить: astra-nexus-team-resume {snapshot.run_id}")
        return "\n".join(lines)

    def _task_started_reply(self) -> str:
        if self.config.atmosphere_mode in {"minimal", "result_snippet", "off"}:
            return "Принял задачу. Команда начала работу."
        return TeamAtmosphereRenderer(self.config.atmosphere).task_started_reply()

    async def _send_result_snippets(
        self,
        *,
        chat_id: int,
        snapshot: TeamJobSnapshot,
    ) -> None:
        for result in self._workspace_results_payload(snapshot.workspace_path):
            role = str(result.get("role") or "")
            content = _clean_final_answer_text(str(result.get("content") or ""))
            snippet = _preview_text(content, limit=self.config.atmosphere_snippet_max_chars)
            if not role or snippet == "нет":
                continue
            await self._send(
                TelegramOutgoingMessage(
                    chat_id=chat_id,
                    text=f"[{role}] {snippet}",
                    send_typing=True,
                )
            )

    def _completed_final_text(self, snapshot: TeamJobSnapshot) -> str:
        final_text = _clean_final_answer_text(snapshot.final_text or "")
        if final_text:
            return final_text

        for result in reversed(self._workspace_results_payload(snapshot.workspace_path)):
            if result.get("role") != AgentRole.FINAL_COMPOSER.value:
                continue
            final_text = _clean_final_answer_text(str(result.get("content") or ""))
            if final_text:
                return final_text

        artifact_final = self._workspace_artifact_final_answer(snapshot.workspace_path)
        if artifact_final:
            return artifact_final
        return "Команда завершилась, но финальный ответ пуст. Детали отправлены в log chat."

    def _completed_final_render(self, snapshot: TeamJobSnapshot):
        final_text = self._completed_final_text(snapshot)
        structured_answer = self._workspace_final_structured_answer(snapshot.workspace_path)
        return render_answer_for_telegram(final_text, structured_answer=structured_answer)

    def _workspace_final_structured_answer(self, workspace_path: Path | None) -> dict[str, Any]:
        for result in reversed(self._workspace_results_payload(workspace_path)):
            if result.get("role") != AgentRole.FINAL_COMPOSER.value:
                continue
            metadata = result.get("metadata")
            if not isinstance(metadata, dict):
                return {}
            provider_response = metadata.get("provider_response")
            if not isinstance(provider_response, dict):
                return {}
            structured = provider_response.get("structured_answer")
            return structured if isinstance(structured, dict) else {}
        return {}

    def _output_requested_as_file(self, snapshot: TeamJobSnapshot) -> bool:
        payload = self._workspace_run_payload(snapshot.workspace_path)
        metadata = payload.get("runtime_metadata") if payload else None
        if not isinstance(metadata, dict):
            return False
        return bool(metadata.get("output_requested_as_file"))

    def _requested_output_format(self, snapshot: TeamJobSnapshot) -> str:
        payload = self._workspace_run_payload(snapshot.workspace_path)
        metadata = payload.get("runtime_metadata") if payload else None
        if not isinstance(metadata, dict):
            return "unknown"
        return str(metadata.get("requested_output_format") or "unknown")

    def _registry_status_text(self, entry: TeamRunRegistryEntry) -> str:
        lines = [
            "Активная задача: нет.",
            "Кто работает: никто.",
            f"Последний run: {entry.status}.",
            f"run_id: {entry.run_id}",
            f"workspace: {entry.workspace_path}",
        ]
        if entry.provider:
            lines.append(f"provider: {entry.provider}")
        if entry.finished_at is not None:
            lines.append(f"finished: {entry.finished_at.isoformat()}")
        if entry.status == "completed":
            lines.append(
                "Последний результат: "
                f"{_preview_text(_clean_final_answer_text(entry.final_result or ''))}"
            )
            if entry.artifacts_count:
                lines.append(f"artifacts: {entry.artifacts_count}")
                if entry.primary_artifact_path is not None:
                    lines.append(f"primary_artifact: {entry.primary_artifact_path}")
        elif entry.status == "failed":
            lines.append("Последний результат: задача завершилась с ошибкой.")
        elif entry.status == "cancelled":
            lines.append("Последний результат: задача отменена.")
        return "\n".join(lines)

    def _runs_text(self, entries: list[TeamRunRegistryEntry]) -> str:
        if not entries:
            return "Пока нет сохранённых запусков для этого чата."
        lines = ["Последние запуски команды:"]
        for entry in entries:
            lines.extend(
                [
                    "",
                    f"- {entry.status}: {entry.title}",
                    f"  run_id: {entry.run_id}",
                    f"  created: {_datetime_text(entry.created_at)}",
                    f"  finished: {_datetime_text(entry.finished_at)}",
                    f"  workspace: {entry.workspace_path}",
                    f"  artifacts: {entry.artifacts_count}",
                ]
            )
            if entry.primary_artifact_path is not None:
                lines.append(f"  primary_artifact: {entry.primary_artifact_path}")
        return "\n".join(lines)

    def _help_text(self) -> str:
        return "\n".join(
            [
                "Команды AI-команды:",
                "/status — активная задача или последний run.",
                "/runs — последние 5 запусков.",
                "/health — состояние Telegram runtime.",
                "/stopall — остановить активную задачу.",
                "",
                "Обычный текст может быть коротким сообщением или задачей. "
                "Если хочешь запустить команду, формулируй действие явно: сделай, проверь, "
                "составь, проанализируй.",
            ]
        )

    def _health_text(self, *, session_id: str) -> str:
        active = self.jobs.active(session_id)
        last_completed = self._last_job_or_registry_run_id(
            self.jobs.last_completed(session_id),
            self.run_registry.last_completed(session_id=session_id),
        )
        last_failed = self._last_job_or_registry_run_id(
            self.jobs.last_failed(session_id),
            self.run_registry.last_failed(session_id=session_id),
        )
        last_cancelled = self._last_job_or_registry_run_id(
            self.jobs.last_cancelled(session_id),
            self.run_registry.last_cancelled(session_id=session_id),
        )
        return "\n".join(
            [
                "AI Team health:",
                f"provider: {self.config.provider}",
                f"active_job: {'yes' if active is not None else 'no'}",
                f"last_completed: {last_completed or 'нет'}",
                f"last_failed: {last_failed or 'нет'}",
                f"last_cancelled: {last_cancelled or 'нет'}",
                f"runs_dir: {self.config.workspace_root}",
                f"log_chat: {'yes' if self.config.log_chat_id is not None else 'no'}",
            ]
        )

    def _last_job_or_registry_run_id(
        self,
        job: TeamJobSnapshot | None,
        entry: TeamRunRegistryEntry | None,
    ) -> str | None:
        if job is not None:
            return job.run_id or job.job_id
        if entry is not None:
            return entry.run_id
        return None

    async def _send_completed_artifacts(
        self,
        *,
        chat_id: int,
        snapshot: TeamJobSnapshot,
    ) -> None:
        payload = self._workspace_run_payload(snapshot.workspace_path)
        if not payload:
            return

        artifacts_count = _int_or_zero(payload.get("artifacts_count"))
        artifacts_dir = _path_or_none(payload.get("artifacts_dir"))
        primary_artifact_path = _path_or_none(payload.get("primary_artifact_path"))
        artifacts_index_path = _path_or_none(payload.get("artifacts_index_path"))
        if not artifacts_count or artifacts_dir is None:
            return

        await self._send_artifact_log_summary(
            snapshot=snapshot,
            artifacts_count=artifacts_count,
            artifacts_dir=artifacts_dir,
            primary_artifact_path=primary_artifact_path,
        )

        if self._output_requested_as_file(snapshot):
            if not self.config.send_requested_files:
                return
            requested_path = self._requested_artifact_path(payload)
            if requested_path is None:
                return
            await self._send(
                TelegramOutgoingMessage(
                    chat_id=chat_id,
                    text="Готово, собрал файл. Проверь, всё лежит внутри.",
                    send_typing=True,
                )
            )
            await self._send_document(chat_id=chat_id, path=requested_path)
            return

        if not self.config.send_internal_artifacts:
            return

        lines = [
            f"Файлы результата сохранены: {artifacts_dir}",
            f"artifacts: {artifacts_count}",
        ]
        if primary_artifact_path is not None:
            lines.append(f"primary_artifact: {primary_artifact_path}")
        await self._send(
            TelegramOutgoingMessage(
                chat_id=chat_id,
                text="\n".join(lines),
                send_typing=True,
            )
        )

        for path in (primary_artifact_path, artifacts_index_path):
            if path is not None:
                await self._send_document(chat_id=chat_id, path=path)

    async def _send_artifact_log_summary(
        self,
        *,
        snapshot: TeamJobSnapshot,
        artifacts_count: int,
        artifacts_dir: Path,
        primary_artifact_path: Path | None,
    ) -> None:
        if self.config.log_chat_id is None:
            return
        details = [
            f"job_id={snapshot.job_id}",
            f"run_id={snapshot.run_id or 'pending'}",
            f"session_id={snapshot.session_id}",
            f"artifacts={artifacts_count}",
            f"artifacts_dir={artifacts_dir}",
        ]
        if primary_artifact_path is not None:
            details.append(f"primary_artifact={primary_artifact_path}")
        await self._send(
            TelegramOutgoingMessage(
                chat_id=self.config.log_chat_id,
                text=f"[Лог] artifacts_saved ({'; '.join(details)})",
                channel=TeamMessageChannel.LOG_CHAT,
            )
        )

    def _requested_artifact_path(self, payload: dict[str, Any]) -> Path | None:
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, list):
            return None
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            if artifact.get("artifact_type") != "requested_output":
                continue
            return _path_or_none(artifact.get("path"))
        return None

    def _workspace_run_payload(self, workspace_path: Path | None) -> dict[str, Any]:
        payload = self._workspace_json(workspace_path, "run.json")
        return payload if isinstance(payload, dict) else {}

    def _workspace_results_payload(self, workspace_path: Path | None) -> list[dict[str, Any]]:
        payload = self._workspace_json(workspace_path, "results.json")
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def _workspace_json(self, workspace_path: Path | None, filename: str) -> Any:
        if workspace_path is None:
            return None
        json_path = workspace_path / filename
        try:
            return json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _workspace_artifact_final_answer(self, workspace_path: Path | None) -> str:
        if workspace_path is None:
            return ""
        final_answer_path = workspace_path / "artifacts" / "final_answer.md"
        try:
            return _clean_final_answer_text(final_answer_path.read_text(encoding="utf-8"))
        except Exception:
            return ""

    def _technical_error_text(self, snapshot: TeamJobSnapshot) -> str | None:
        payload = self._workspace_run_payload(snapshot.workspace_path)
        error = payload.get("error_message") if payload else None
        if error:
            return str(error)
        return snapshot.error_message

    def _session_id(self, chat_id: int) -> str:
        return str(self._resolve_migrated_chat_id(chat_id))

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
            ("/help", "/help", ()),
            ("/health", "/health", ()),
            ("сделай краткий план AI-команды", "сделай краткий план AI-команды", ()),
            ("/status", "/status", ()),
            ("/runs", "/runs", ()),
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


def _document_path(document: Any) -> Path:
    if isinstance(document, Path):
        return document
    path = getattr(document, "path", None)
    if path is not None:
        return Path(path)
    return Path(str(document))


def _int_or_zero(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _path_or_none(value: Any) -> Path | None:
    if value is None or value == "":
        return None
    return Path(str(value))


def _preview_text(text: str, *, limit: int = 180) -> str:
    compact = " ".join(text.split())
    if not compact:
        return "нет"
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1].rstrip()}..."


def _normalize_dedupe_text(text: str) -> str:
    return " ".join(str(text or "").split())


def _clean_final_answer_text(text: str) -> str:
    compact = str(text or "").strip()
    for prefix in (
        "fake:final_composer:",
        "nodriver_team:final_composer:",
        "final_composer:",
    ):
        if compact.lower().startswith(prefix):
            compact = compact[len(prefix) :].strip()
            break
    if "Ты агент в системе" in compact:
        return ""
    return compact


def _datetime_text(value: Any) -> str:
    return value.isoformat() if value is not None else "нет"


if __name__ == "__main__":
    raise SystemExit(main_preview())
