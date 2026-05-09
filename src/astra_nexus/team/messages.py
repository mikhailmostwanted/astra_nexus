from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from astra_nexus.team.models import AgentProfile, AgentRole, RunEvent, RunEventType, utc_now
from astra_nexus.utils.ids import new_id


class TeamMessageType(StrEnum):
    AGENT_SAYS = "agent_says"
    AGENT_THINKS = "agent_thinks"
    AGENT_STARTED = "agent_started"
    AGENT_FINISHED = "agent_finished"
    AGENT_RETRY = "agent_retry"
    AGENT_FAILED = "agent_failed"
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    SYSTEM_LOG = "system_log"
    USER_VISIBLE_STATUS = "user_visible_status"


class TeamMessageChannel(StrEnum):
    MAIN_CHAT = "main_chat"
    LOG_CHAT = "log_chat"
    DEBUG = "debug"


@dataclass(frozen=True)
class TeamMessage:
    run_id: str
    channel: TeamMessageChannel
    type: TeamMessageType
    text: str
    id: str = field(default_factory=lambda: new_id("team_message"))
    author_name: str | None = None
    author_role: AgentRole | None = None
    event_id: str | None = None
    agent_task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)


class TeamMessageSink(Protocol):
    def publish(self, message: TeamMessage) -> None: ...


class NullTeamMessageSink:
    def publish(self, message: TeamMessage) -> None:
        return None


class InMemoryTeamMessageSink:
    def __init__(self, seed: Sequence[TeamMessage] | None = None) -> None:
        self.messages = list(seed or ())

    def publish(self, message: TeamMessage) -> None:
        self.messages.append(message)


class CompositeTeamMessageSink:
    def __init__(self, sinks: Iterable[TeamMessageSink]) -> None:
        self.sinks = tuple(sinks)

    def publish(self, message: TeamMessage) -> None:
        for sink in self.sinks:
            sink.publish(message)


class TeamMessageRenderer:
    def __init__(self, profiles_by_role: dict[AgentRole, AgentProfile]) -> None:
        self.profiles_by_role = profiles_by_role

    def render_event(self, event: RunEvent) -> list[TeamMessage]:
        if event.type == RunEventType.RUN_STARTED:
            return [
                self._message(
                    event,
                    channel=TeamMessageChannel.MAIN_CHAT,
                    message_type=TeamMessageType.RUN_STARTED,
                    text="Принял задачу. Запускаю команду.",
                    author_name="Команда",
                ),
                self._log_message(event, TeamMessageType.RUN_STARTED),
            ]
        if event.type == RunEventType.RUN_FINISHED:
            return [
                self._message(
                    event,
                    channel=TeamMessageChannel.MAIN_CHAT,
                    message_type=TeamMessageType.RUN_FINISHED,
                    text="Готово, финальная версия собрана.",
                    author_name="Команда",
                ),
                self._log_message(event, TeamMessageType.RUN_FINISHED),
            ]
        if event.type == RunEventType.RUN_FAILED:
            return [self._log_message(event, TeamMessageType.SYSTEM_LOG)]
        if event.type == RunEventType.AGENT_STARTED:
            return [
                self._agent_message(
                    event,
                    message_type=TeamMessageType.AGENT_STARTED,
                    text=self._agent_started_text(event.agent_role),
                ),
                self._log_message(event, TeamMessageType.AGENT_STARTED),
            ]
        if event.type == RunEventType.AGENT_FINISHED:
            return [
                self._agent_message(
                    event,
                    message_type=TeamMessageType.AGENT_FINISHED,
                    text=self._agent_finished_text(event.agent_role),
                ),
                self._log_message(event, TeamMessageType.AGENT_FINISHED),
            ]
        if event.type == RunEventType.AGENT_RETRY_SCHEDULED:
            return [
                self._agent_message(
                    event,
                    message_type=TeamMessageType.AGENT_RETRY,
                    text="Поймал временный сбой, пробую ещё раз.",
                ),
                self._log_message(event, TeamMessageType.AGENT_RETRY),
            ]
        if event.type == RunEventType.AGENT_RETRY_STARTED:
            return [self._log_message(event, TeamMessageType.AGENT_RETRY)]
        if event.type == RunEventType.AGENT_FAILED:
            return [
                self._agent_message(
                    event,
                    message_type=TeamMessageType.AGENT_FAILED,
                    text="На этом шаге упёрся в ошибку. Run сохранён, его можно продолжить.",
                ),
                self._log_message(
                    event,
                    TeamMessageType.AGENT_FAILED,
                    text=(
                        f"{event.message} Можно продолжить: astra-nexus-team-resume {event.run_id}"
                    ),
                ),
            ]
        return [self._log_message(event, TeamMessageType.SYSTEM_LOG)]

    def _message(
        self,
        event: RunEvent,
        *,
        channel: TeamMessageChannel,
        message_type: TeamMessageType,
        text: str,
        author_name: str | None = None,
        author_role: AgentRole | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TeamMessage:
        return TeamMessage(
            run_id=event.run_id,
            channel=channel,
            type=message_type,
            text=text,
            author_name=author_name,
            author_role=author_role,
            event_id=event.id,
            agent_task_id=event.agent_task_id,
            metadata=metadata or {},
            created_at=event.created_at,
        )

    def _agent_message(
        self,
        event: RunEvent,
        *,
        message_type: TeamMessageType,
        text: str,
    ) -> TeamMessage:
        profile = self._profile(event.agent_role)
        return self._message(
            event,
            channel=TeamMessageChannel.MAIN_CHAT,
            message_type=message_type,
            text=text,
            author_name=self._short_name(profile),
            author_role=event.agent_role,
        )

    def _log_message(
        self,
        event: RunEvent,
        message_type: TeamMessageType,
        text: str | None = None,
    ) -> TeamMessage:
        metadata = {
            "event_type": event.type.value,
            "run_id": event.run_id,
            "agent_role": event.agent_role.value if event.agent_role is not None else None,
            "agent_task_id": event.agent_task_id,
            **event.payload,
        }
        return self._message(
            event,
            channel=TeamMessageChannel.LOG_CHAT,
            message_type=message_type,
            text=text or event.message,
            author_name="Лог",
            author_role=event.agent_role,
            metadata=metadata,
        )

    def _agent_started_text(self, role: AgentRole | None) -> str:
        profile = self._profile(role)
        if profile is not None and profile.main_chat_intro:
            return profile.main_chat_intro
        if role == AgentRole.COORDINATOR:
            return "Босс, принял задачу. Сейчас разложу её на рабочий маршрут."
        if role == AgentRole.ANALYST:
            return "Разберу вводные: факты, ограничения и допущения."
        if role == AgentRole.CRITIC:
            return "Я проверю слабые места: логику, риски, пропуски и спорные места."
        if role == AgentRole.EDITOR:
            return "Забираю выводы и собираю более чистую версию."
        if role == AgentRole.QA_CONTROLLER:
            return "Сейчас проверю, не развалилось ли решение по требованиям."
        if role == AgentRole.FINAL_COMPOSER:
            return "Собираю финальный ответ в нормальный вид."
        return "Начинаю следующий шаг."

    def _agent_finished_text(self, role: AgentRole | None) -> str:
        if role == AgentRole.COORDINATOR:
            return "Маршрут готов, передаю дальше."
        if role == AgentRole.ANALYST:
            return "Разбор готов, можно проверять слабые места."
        if role == AgentRole.CRITIC:
            return "Замечания собраны, передаю на улучшение."
        if role == AgentRole.EDITOR:
            return "Черновик стал чище, отдаю на контроль качества."
        if role == AgentRole.QA_CONTROLLER:
            return "Проверка завершена, можно собирать финал."
        if role == AgentRole.FINAL_COMPOSER:
            return "Финальный ответ собран."
        return "Шаг завершён."

    def _profile(self, role: AgentRole | None) -> AgentProfile | None:
        if role is None:
            return None
        return self.profiles_by_role.get(role)

    def _short_name(self, profile: AgentProfile | None) -> str | None:
        if profile is None:
            return None
        short_name = profile.short_name or profile.display_name.split("/", maxsplit=1)[0]
        return short_name.strip()
