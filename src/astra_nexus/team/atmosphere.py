from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from astra_nexus.config.settings import Settings
from astra_nexus.team.dialogue import TeamDialogueTurn, dialogue_turn_to_messages
from astra_nexus.team.messages import (
    TeamMessage,
    TeamMessageChannel,
    TeamMessageSink,
    TeamMessageType,
)
from astra_nexus.team.models import AgentRole, RunEvent, RunEventType, utc_now


class AtmosphereLevel(StrEnum):
    MINIMAL = "minimal"
    NORMAL = "normal"
    CINEMATIC = "cinematic"


@dataclass(frozen=True)
class AgentVoiceStyle:
    role: AgentRole
    short_name: str
    tone: str
    start_text: str
    finish_text: str
    handoff_text: str = ""
    emoji: str = ""


@dataclass(frozen=True)
class AtmosphereProfile:
    enabled: bool = True
    level: AtmosphereLevel = AtmosphereLevel.NORMAL
    send_delays: bool = False
    min_delay_seconds: float = 0.3
    max_delay_seconds: float = 1.4
    emoji_enabled: bool = False
    max_main_messages_per_run: int = 20
    suppress_technical_in_main: bool = True
    voice_styles: Mapping[AgentRole, AgentVoiceStyle] = field(
        default_factory=lambda: default_agent_voice_styles()
    )

    @classmethod
    def from_settings(cls, settings: Settings) -> AtmosphereProfile:
        min_delay = max(0.0, float(settings.team_atmosphere_min_delay_seconds))
        max_delay = max(0.0, float(settings.team_atmosphere_max_delay_seconds))
        if max_delay < min_delay:
            max_delay = min_delay
        return cls(
            enabled=settings.team_atmosphere_enabled,
            level=AtmosphereLevel(settings.team_atmosphere_level),
            send_delays=settings.team_atmosphere_send_delays,
            min_delay_seconds=min_delay,
            max_delay_seconds=max_delay,
            emoji_enabled=settings.team_atmosphere_emoji_enabled,
            max_main_messages_per_run=max(
                1,
                int(settings.team_atmosphere_max_main_messages_per_run),
            ),
            suppress_technical_in_main=settings.team_atmosphere_suppress_technical_in_main,
        )


@dataclass(frozen=True)
class AtmosphereMessage:
    run_id: str
    channel: TeamMessageChannel
    type: TeamMessageType
    text: str
    author_name: str | None = None
    author_role: AgentRole | None = None
    is_technical: bool = False
    always_send: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_team_message(self) -> TeamMessage:
        metadata = {
            "atmosphere": True,
            "is_technical": self.is_technical,
            "always_send": self.always_send,
            **self.metadata,
        }
        return TeamMessage(
            run_id=self.run_id,
            channel=self.channel,
            type=self.type,
            text=self.text,
            author_name=self.author_name,
            author_role=self.author_role,
            metadata=metadata,
            created_at=utc_now(),
        )


class TeamAtmosphereRenderer:
    def __init__(self, profile: AtmosphereProfile | None = None) -> None:
        self.profile = profile or AtmosphereProfile()

    def render_dialogue_turn(self, turn: TeamDialogueTurn) -> list[AtmosphereMessage]:
        messages = dialogue_turn_to_messages(turn)
        return [
            atmosphere_message
            for message in messages
            for atmosphere_message in self.render_team_message(message)
        ]

    def render_event(self, event: RunEvent) -> list[AtmosphereMessage]:
        metadata = {
            "event_type": event.type.value,
            "run_id": event.run_id,
            "agent_role": event.agent_role.value if event.agent_role is not None else None,
            "agent_task_id": event.agent_task_id,
            **event.payload,
        }
        messages = [
            AtmosphereMessage(
                run_id=event.run_id,
                channel=TeamMessageChannel.LOG_CHAT,
                type=_event_message_type(event.type),
                text=event.message,
                author_name="Лог",
                author_role=event.agent_role,
                is_technical=True,
                metadata=metadata,
            )
        ]
        if not self.profile.enabled:
            return messages
        if self.profile.suppress_technical_in_main:
            return messages
        if event.type in {RunEventType.AGENT_RETRY_SCHEDULED, RunEventType.AGENT_FAILED}:
            messages.append(
                AtmosphereMessage(
                    run_id=event.run_id,
                    channel=TeamMessageChannel.MAIN_CHAT,
                    type=_event_message_type(event.type),
                    text=_technical_main_text(event.type),
                    author_name=_short_name_for_role(event.agent_role),
                    author_role=event.agent_role,
                    is_technical=True,
                    metadata=metadata,
                )
            )
        return messages

    def render_team_message(self, message: TeamMessage) -> list[AtmosphereMessage]:
        if not self.profile.enabled:
            return [_from_team_message(message)]
        if message.channel != TeamMessageChannel.MAIN_CHAT:
            return [_from_team_message(message, is_technical=True)]
        if self._is_technical_main_message(message) and self.profile.suppress_technical_in_main:
            return []
        if not self._visible_at_level(message):
            return []

        human_text = self._human_text(message)
        if not human_text:
            return []
        return [
            AtmosphereMessage(
                run_id=message.run_id,
                channel=message.channel,
                type=message.type,
                text=human_text,
                author_name=message.author_name,
                author_role=message.author_role,
                is_technical=False,
                always_send=self._is_final_signal(message),
                metadata={
                    **message.metadata,
                    "atmosphere_level": self.profile.level.value,
                },
            )
        ]

    def human_casual_reply(self) -> str:
        return "Босс, я на связи. Можем спокойно обсудить или сразу превратить мысль в задачу."

    def file_without_caption_reply(self) -> str:
        return (
            "Босс, файл вижу, но задачи к нему нет. Напиши, что с ним сделать: "
            "проверить, переписать, сократить, сравнить или собрать итоговый вариант."
        )

    def task_started_reply(self) -> str:
        if self.profile.level == AtmosphereLevel.MINIMAL:
            return "Босс, вижу задачу. Команда начала работу."
        return "Босс, вижу задачу. Сначала разложу её на части."

    def stopall_reply(self, *, had_active_task: bool) -> str:
        if had_active_task:
            return "Остановил активную задачу. Команда вернулась в общий чат."
        return "Активных задач сейчас нет."

    def _human_text(self, message: TeamMessage) -> str:
        if message.type != TeamMessageType.AGENT_SAYS:
            return message.text
        if _is_attachment_notice(message.text):
            return message.text
        if message.author_role is None:
            if self._is_final_signal(message):
                return self._with_emoji("Финал готов. Ниже собранный вариант.", None)
            return message.text

        voice = self.profile.voice_styles.get(message.author_role)
        if voice is None:
            return message.text
        style = str(message.metadata.get("style") or "")
        phase = str(message.metadata.get("phase") or "")
        if style == "summary":
            text = voice.finish_text
            if message.author_role == AgentRole.QA_CONTROLLER and "доработ" in message.text:
                text = "Нужна одна доработка перед финалом. Возвращаю редактору конкретные правки."
            if message.author_role == AgentRole.FINAL_COMPOSER:
                text = "Финал готов. Ниже собранный вариант."
        elif phase == "completed":
            text = "Финал готов. Ниже собранный вариант."
        else:
            text = voice.start_text
        return self._with_emoji(text, voice)

    def _visible_at_level(self, message: TeamMessage) -> bool:
        if self._is_final_signal(message):
            return True
        if self.profile.level != AtmosphereLevel.MINIMAL:
            return True
        return message.author_role in {
            AgentRole.COORDINATOR,
            AgentRole.CRITIC,
            AgentRole.FINAL_COMPOSER,
        }

    def _is_final_signal(self, message: TeamMessage) -> bool:
        if (
            message.author_role == AgentRole.FINAL_COMPOSER
            and message.metadata.get("style") == "summary"
        ):
            return True
        return message.author_role is None and message.metadata.get("phase") == "completed"

    def _is_technical_main_message(self, message: TeamMessage) -> bool:
        if message.metadata.get("event_type"):
            return True
        return message.type in {
            TeamMessageType.AGENT_RETRY,
            TeamMessageType.AGENT_FAILED,
            TeamMessageType.AGENT_STARTED,
            TeamMessageType.AGENT_FINISHED,
            TeamMessageType.RUN_STARTED,
            TeamMessageType.RUN_FINISHED,
            TeamMessageType.SYSTEM_LOG,
        }

    def _with_emoji(self, text: str, voice: AgentVoiceStyle | None) -> str:
        if not self.profile.emoji_enabled or voice is None or not voice.emoji:
            return text
        return f"{voice.emoji} {text}"


class AtmosphereTeamMessageSink:
    def __init__(
        self,
        sink: TeamMessageSink,
        renderer: TeamAtmosphereRenderer | None = None,
    ) -> None:
        self.sink = sink
        self.renderer = renderer or TeamAtmosphereRenderer()
        self._main_counts_by_run: dict[str, int] = {}

    def publish(self, message: TeamMessage) -> None:
        for atmosphere_message in self.renderer.render_team_message(message):
            if not self._can_publish(atmosphere_message):
                continue
            self.sink.publish(atmosphere_message.to_team_message())

    def _can_publish(self, message: AtmosphereMessage) -> bool:
        if message.channel != TeamMessageChannel.MAIN_CHAT:
            return True
        if message.always_send:
            return True
        current = self._main_counts_by_run.get(message.run_id, 0)
        if current >= self.renderer.profile.max_main_messages_per_run:
            return False
        self._main_counts_by_run[message.run_id] = current + 1
        return True


def default_agent_voice_styles() -> dict[AgentRole, AgentVoiceStyle]:
    return {
        AgentRole.COORDINATOR: AgentVoiceStyle(
            role=AgentRole.COORDINATOR,
            short_name="Артём",
            tone="спокойный руководитель, коротко ставит задачу и передаёт работу",
            start_text="Босс, вижу задачу. Сначала разложу её на части.",
            finish_text="Маршрут готов. Передаю работу дальше по цепочке.",
            handoff_text="Подключаю аналитика и критика, пусть проверят с двух сторон.",
            emoji="🧭",
        ),
        AgentRole.ANALYST: AgentVoiceStyle(
            role=AgentRole.ANALYST,
            short_name="Ирина",
            tone="аналитик, говорит по делу, без лишней драматизации",
            start_text="Разберу вводные, ограничения и факты без лишней драматизации.",
            finish_text="Разбор готов. Дальше стоит проверить риски и пробелы.",
            handoff_text="Передаю критику основу для проверки.",
            emoji="🔎",
        ),
        AgentRole.CRITIC: AgentVoiceStyle(
            role=AgentRole.CRITIC,
            short_name="Вера",
            tone="строгий проверяющий, ищет слабые места",
            start_text="Проверяю слабые места: критерии, риски и недосказанность.",
            finish_text="Слабые места собраны. Передаю редактору, чтобы усилить результат.",
            handoff_text="Нашла, что нужно поправить перед чистовой версией.",
            emoji="⚠️",
        ),
        AgentRole.EDITOR: AgentVoiceStyle(
            role=AgentRole.EDITOR,
            short_name="Лина",
            tone="улучшает и переписывает, формулирует аккуратно",
            start_text="Забираю правки, сейчас соберу более чистую версию.",
            finish_text="Формулировки выровняла. Отдаю на контроль качества.",
            handoff_text="Передаю QA уже очищенный вариант.",
            emoji="✍️",
        ),
        AgentRole.QA_CONTROLLER: AgentVoiceStyle(
            role=AgentRole.QA_CONTROLLER,
            short_name="Максим",
            tone="проверяет готовность, риски и соответствие задаче",
            start_text="Сверяю результат с задачей, рисками и готовностью к отдаче.",
            finish_text="Проверка пройдена. Можно собирать финал.",
            handoff_text="Финальному сборщику можно брать чистовую основу.",
            emoji="✅",
        ),
        AgentRole.FINAL_COMPOSER: AgentVoiceStyle(
            role=AgentRole.FINAL_COMPOSER,
            short_name="Саша",
            tone="собирает финал, говорит уверенно и чисто",
            start_text="Финал собираю в чистый ответ без внутренней кухни.",
            finish_text="Финал готов. Ниже собранный вариант.",
            handoff_text="Отдаю пользователю готовый результат.",
            emoji="📌",
        ),
    }


def _from_team_message(
    message: TeamMessage, *, is_technical: bool | None = None
) -> AtmosphereMessage:
    return AtmosphereMessage(
        run_id=message.run_id,
        channel=message.channel,
        type=message.type,
        text=message.text,
        author_name=message.author_name,
        author_role=message.author_role,
        is_technical=is_technical
        if is_technical is not None
        else message.channel != TeamMessageChannel.MAIN_CHAT,
        metadata=message.metadata,
    )


def _event_message_type(event_type: RunEventType) -> TeamMessageType:
    if event_type == RunEventType.RUN_STARTED:
        return TeamMessageType.RUN_STARTED
    if event_type == RunEventType.RUN_FINISHED:
        return TeamMessageType.RUN_FINISHED
    if event_type == RunEventType.AGENT_STARTED:
        return TeamMessageType.AGENT_STARTED
    if event_type == RunEventType.AGENT_FINISHED:
        return TeamMessageType.AGENT_FINISHED
    if event_type in {RunEventType.AGENT_RETRY_SCHEDULED, RunEventType.AGENT_RETRY_STARTED}:
        return TeamMessageType.AGENT_RETRY
    if event_type == RunEventType.AGENT_FAILED:
        return TeamMessageType.AGENT_FAILED
    return TeamMessageType.SYSTEM_LOG


def _technical_main_text(event_type: RunEventType) -> str:
    if event_type == RunEventType.AGENT_RETRY_SCHEDULED:
        return "Поймал временный сбой, пробую ещё раз."
    if event_type == RunEventType.AGENT_FAILED:
        return "На этом шаге упёрся в ошибку. Run сохранён, его можно продолжить."
    return "Команда обновила статус работы."


def _short_name_for_role(role: AgentRole | None) -> str | None:
    if role is None:
        return None
    style = default_agent_voice_styles().get(role)
    return style.short_name if style else role.value


def _is_attachment_notice(text: str) -> bool:
    return "Файл вижу" in text or "файл вижу" in text
