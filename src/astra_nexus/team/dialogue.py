from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from astra_nexus.team.attachments import (
    TeamAttachmentExtractionStatus,
    TeamInputAttachment,
)
from astra_nexus.team.messages import TeamMessage, TeamMessageChannel, TeamMessageType
from astra_nexus.team.models import AgentProfile, AgentRole, utc_now
from astra_nexus.utils.ids import new_id


class TeamDialoguePhase(StrEnum):
    INTAKE = "intake"
    COORDINATION = "coordination"
    ANALYSIS = "analysis"
    CRITIQUE = "critique"
    REVISION = "revision"
    QA = "qa"
    FINALIZATION = "finalization"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TeamDialogueStyle(StrEnum):
    WORKING = "working"
    SUMMARY = "summary"
    ERROR = "error"


@dataclass(frozen=True)
class TeamDialogueTurn:
    run_id: str
    agent_role: AgentRole | None
    agent_display_name: str
    phase: TeamDialoguePhase
    text: str
    reply_to_role: AgentRole | None = None
    is_user_visible: bool = True
    is_log_visible: bool = False
    id: str = field(default_factory=lambda: new_id("dialogue_turn"))
    created_at: datetime = field(default_factory=utc_now)
    style: TeamDialogueStyle = TeamDialogueStyle.WORKING
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TeamDialogueTranscript:
    run_id: str
    turns: list[TeamDialogueTurn] = field(default_factory=list)

    def append(self, turn: TeamDialogueTurn) -> None:
        self.turns.append(turn)


ROLE_PHASES = {
    AgentRole.COORDINATOR: TeamDialoguePhase.COORDINATION,
    AgentRole.ANALYST: TeamDialoguePhase.ANALYSIS,
    AgentRole.CRITIC: TeamDialoguePhase.CRITIQUE,
    AgentRole.EDITOR: TeamDialoguePhase.REVISION,
    AgentRole.QA_CONTROLLER: TeamDialoguePhase.QA,
    AgentRole.FINAL_COMPOSER: TeamDialoguePhase.FINALIZATION,
}


START_TEXTS = {
    AgentRole.COORDINATOR: "Понял задачу. Сейчас сформулирую цель и рабочий маршрут для команды.",
    AgentRole.ANALYST: "Разберу вводные и вытащу главное без лишней воды.",
    AgentRole.CRITIC: "Я посмотрю слабые места и что тут может быть не так.",
    AgentRole.EDITOR: "Окей, правлю по замечаниям и собираю более чистый вариант.",
    AgentRole.QA_CONTROLLER: "Проверяю, не потеряли ли смысл и файлы по дороге.",
    AgentRole.FINAL_COMPOSER: "Собираю финальную версию.",
}


FINISH_TEXTS = {
    AgentRole.COORDINATOR: "Маршрут есть, передаю на разбор.",
    AgentRole.ANALYST: "Разбор готов, дальше полезно проверить риски.",
    AgentRole.CRITIC: "Нашла, что стоит проверить и усилить.",
    AgentRole.EDITOR: "Поправила по замечаниям, версия стала чище.",
    AgentRole.QA_CONTROLLER: "Проверка пройдена, можно собирать финал.",
    AgentRole.FINAL_COMPOSER: "Финальный ответ собран.",
}


def build_agent_start_turn(
    *,
    run_id: str,
    profile: AgentProfile,
    attachments: Iterable[TeamInputAttachment] = (),
) -> TeamDialogueTurn:
    text = START_TEXTS[profile.role]
    if profile.role == AgentRole.COORDINATOR and _has_metadata_only_attachment(attachments):
        text = (
            "Файл вижу, но текст из него пока не извлечён. "
            "Буду работать по метаданным и тексту задачи."
        )
    return TeamDialogueTurn(
        run_id=run_id,
        agent_role=profile.role,
        agent_display_name=profile.display_name,
        phase=ROLE_PHASES[profile.role],
        text=text,
        reply_to_role=AgentRole.CRITIC if profile.role == AgentRole.EDITOR else None,
    )


def build_agent_finish_turn(
    *,
    run_id: str,
    profile: AgentProfile,
    needs_revision: bool | None = None,
) -> TeamDialogueTurn:
    text = FINISH_TEXTS[profile.role]
    if profile.role == AgentRole.QA_CONTROLLER:
        text = (
            "Нужна одна доработка перед финалом."
            if needs_revision
            else "Проверка пройдена, можно собирать финал."
        )
    return TeamDialogueTurn(
        run_id=run_id,
        agent_role=profile.role,
        agent_display_name=profile.display_name,
        phase=ROLE_PHASES[profile.role],
        text=text,
        reply_to_role=AgentRole.CRITIC if profile.role == AgentRole.EDITOR else None,
        style=TeamDialogueStyle.SUMMARY,
    )


def build_completed_turn(*, run_id: str) -> TeamDialogueTurn:
    return TeamDialogueTurn(
        run_id=run_id,
        agent_role=None,
        agent_display_name="Команда",
        phase=TeamDialoguePhase.COMPLETED,
        text="Готово, финальная версия собрана.",
        style=TeamDialogueStyle.SUMMARY,
    )


def build_failed_turn(*, run_id: str, profile: AgentProfile) -> TeamDialogueTurn:
    return TeamDialogueTurn(
        run_id=run_id,
        agent_role=profile.role,
        agent_display_name=profile.display_name,
        phase=TeamDialoguePhase.FAILED,
        text="На этом шаге упёрся в ошибку. Run сохранён, его можно продолжить.",
        style=TeamDialogueStyle.ERROR,
    )


def dialogue_turn_to_messages(turn: TeamDialogueTurn) -> list[TeamMessage]:
    messages: list[TeamMessage] = []
    metadata = {
        "dialogue_turn_id": turn.id,
        "phase": turn.phase.value,
        "reply_to_role": turn.reply_to_role.value if turn.reply_to_role is not None else None,
        "style": turn.style.value,
        **turn.metadata,
    }
    if turn.is_user_visible:
        messages.append(
            TeamMessage(
                run_id=turn.run_id,
                channel=TeamMessageChannel.MAIN_CHAT,
                type=TeamMessageType.AGENT_SAYS,
                text=turn.text,
                author_name=_short_name(turn.agent_display_name),
                author_role=turn.agent_role,
                metadata=metadata,
                created_at=turn.created_at,
            )
        )
    if turn.is_log_visible:
        messages.append(
            TeamMessage(
                run_id=turn.run_id,
                channel=TeamMessageChannel.LOG_CHAT,
                type=TeamMessageType.SYSTEM_LOG,
                text=turn.text,
                author_name="Лог",
                author_role=turn.agent_role,
                metadata=metadata,
                created_at=turn.created_at,
            )
        )
    return messages


def dialogue_transcript_payload(turns: list[TeamDialogueTurn], *, run_id: str) -> dict[str, Any]:
    return {"run_id": run_id, "turns": [dialogue_turn_payload(turn) for turn in turns]}


def dialogue_turn_payload(turn: TeamDialogueTurn) -> dict[str, Any]:
    return {
        "turn_id": turn.id,
        "timestamp": turn.created_at.isoformat(),
        "run_id": turn.run_id,
        "agent_role": turn.agent_role.value if turn.agent_role is not None else None,
        "agent_display_name": turn.agent_display_name,
        "phase": turn.phase.value,
        "text": turn.text,
        "reply_to_role": turn.reply_to_role.value if turn.reply_to_role is not None else None,
        "is_user_visible": turn.is_user_visible,
        "is_log_visible": turn.is_log_visible,
        "style": turn.style.value,
        "metadata": turn.metadata,
    }


def dialogue_turn_from_payload(payload: dict[str, Any]) -> TeamDialogueTurn:
    return TeamDialogueTurn(
        id=payload["turn_id"],
        run_id=payload["run_id"],
        agent_role=AgentRole(payload["agent_role"]) if payload.get("agent_role") else None,
        agent_display_name=payload["agent_display_name"],
        phase=TeamDialoguePhase(payload["phase"]),
        text=payload["text"],
        reply_to_role=AgentRole(payload["reply_to_role"]) if payload.get("reply_to_role") else None,
        is_user_visible=payload.get("is_user_visible", True),
        is_log_visible=payload.get("is_log_visible", False),
        created_at=datetime.fromisoformat(payload["timestamp"]),
        style=TeamDialogueStyle(payload.get("style", TeamDialogueStyle.WORKING.value)),
        metadata=payload.get("metadata", {}),
    )


def dialogue_markdown(turns: list[TeamDialogueTurn]) -> str:
    sections = ["# Team Chat", ""]
    if not turns:
        sections.extend(["No dialogue turns.", ""])
        return "\n".join(sections)

    for turn in turns:
        timestamp = turn.created_at.isoformat()
        author = _short_name(turn.agent_display_name)
        reply = f" -> {turn.reply_to_role.value}" if turn.reply_to_role is not None else ""
        sections.append(f"- `{timestamp}` [{author}] ({turn.phase.value}{reply}) {turn.text}")
    sections.append("")
    return "\n".join(sections)


def _has_metadata_only_attachment(attachments: Iterable[TeamInputAttachment]) -> bool:
    for attachment in attachments:
        if attachment.extracted_text:
            continue
        if attachment.extraction_status in {
            TeamAttachmentExtractionStatus.METADATA_ONLY,
            TeamAttachmentExtractionStatus.FAILED,
            TeamAttachmentExtractionStatus.NOT_NEEDED,
        }:
            return True
    return False


def _short_name(display_name: str) -> str:
    return display_name.split("/", maxsplit=1)[0].strip() or display_name
