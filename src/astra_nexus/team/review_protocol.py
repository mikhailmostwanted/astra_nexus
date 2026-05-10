from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from astra_nexus.team.attachments import (
    TeamAttachmentExtractionStatus,
    TeamInputAttachment,
)
from astra_nexus.team.models import AgentRole, utc_now
from astra_nexus.utils.ids import new_id


class TeamReviewSeverity(StrEnum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


@dataclass(frozen=True)
class TeamTaskBrief:
    original_user_input: str
    normalized_goal: str
    expected_output: str
    constraints: tuple[str, ...]
    available_attachments: tuple[str, ...]
    open_questions: tuple[str, ...]
    risk_notes: tuple[str, ...]
    created_by: str
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class TeamQualityCriterion:
    criterion_id: str
    title: str
    description: str
    required: bool
    source_agent: str


@dataclass(frozen=True)
class TeamReviewNote:
    author_role: str
    severity: TeamReviewSeverity
    message: str
    suggested_fix: str
    note_id: str = field(default_factory=lambda: new_id("review_note"))
    target_role: str | None = None
    target_artifact: str | None = None


@dataclass(frozen=True)
class TeamRevisionRequest:
    requested_by: str
    target_role: str
    instructions: str
    related_notes: tuple[str, ...]
    must_fix_before_final: bool


@dataclass(frozen=True)
class TeamReviewDecision:
    approved: bool
    needs_revision: bool
    blocking_notes: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class TeamFinalPackage:
    final_text: str
    brief_summary: str
    applied_revision_count: int
    remaining_limitations: tuple[str, ...]
    quality_check_summary: str


def build_task_brief(
    *,
    original_user_input: str,
    attachments: Sequence[TeamInputAttachment] = (),
    created_by: AgentRole = AgentRole.COORDINATOR,
) -> TeamTaskBrief:
    normalized_goal = _normalize_text(original_user_input) or "Уточнить задачу пользователя."
    expected_output = _expected_output_for(normalized_goal, attachments)
    constraints = [
        "Сохранять исходный смысл задачи пользователя.",
        "Не выдумывать факты и явно отмечать неопределённость.",
        "Учитывать доступные вложения только в пределах извлечённого текста и метаданных.",
    ]
    if attachments:
        constraints.append(f"Учесть вложения: {len(attachments)}.")

    attachment_names = tuple(attachment.original_filename for attachment in attachments)
    open_questions = []
    risk_notes = []
    for attachment in attachments:
        if attachment.extraction_status in {
            TeamAttachmentExtractionStatus.METADATA_ONLY,
            TeamAttachmentExtractionStatus.FAILED,
            TeamAttachmentExtractionStatus.NOT_NEEDED,
        }:
            risk_notes.append(
                f"Текст файла {attachment.original_filename} недоступен полностью; "
                "используются метаданные и извлечённые части."
            )
    if not normalized_goal:
        open_questions.append("Нужно уточнить, какой результат ожидает пользователь.")

    return TeamTaskBrief(
        original_user_input=original_user_input,
        normalized_goal=normalized_goal,
        expected_output=expected_output,
        constraints=tuple(constraints),
        available_attachments=attachment_names,
        open_questions=tuple(open_questions),
        risk_notes=tuple(risk_notes),
        created_by=created_by.value,
    )


def build_quality_criteria(
    *,
    source_agent: AgentRole = AgentRole.COORDINATOR,
) -> tuple[TeamQualityCriterion, ...]:
    source = source_agent.value
    return (
        TeamQualityCriterion(
            criterion_id="qc_goal_match",
            title="Соответствие задаче",
            description="Итог отвечает на исходную задачу и не уходит в сторону.",
            required=True,
            source_agent=source,
        ),
        TeamQualityCriterion(
            criterion_id="qc_constraints",
            title="Учет ограничений",
            description="Ответ учитывает ограничения, файлы, неопределённость и запреты.",
            required=True,
            source_agent=source,
        ),
        TeamQualityCriterion(
            criterion_id="qc_actionability",
            title="Практическая полезность",
            description="Результат можно использовать без дополнительной расшифровки.",
            required=True,
            source_agent=source,
        ),
        TeamQualityCriterion(
            criterion_id="qc_clarity",
            title="Ясность финала",
            description="Финальный текст собран ясно, спокойно и без внутренней кухни.",
            required=True,
            source_agent=source,
        ),
    )


def review_notes_from_critic_result(content: str) -> tuple[TeamReviewNote, ...]:
    message = _first_useful_line(content)
    if not message:
        message = "Критик не вернул явных замечаний; редактору нужно проверить полноту вручную."
        severity = TeamReviewSeverity.MINOR
    else:
        severity = _severity_from_text(message)
    return (
        TeamReviewNote(
            author_role=AgentRole.CRITIC.value,
            severity=severity,
            target_role=AgentRole.EDITOR.value,
            message=message,
            suggested_fix="Учесть замечание критика в редакторской версии перед QA.",
        ),
    )


def revision_requests_from_notes(
    notes: Sequence[TeamReviewNote],
) -> tuple[TeamRevisionRequest, ...]:
    requests = []
    for note in notes:
        if note.target_role != AgentRole.EDITOR.value:
            continue
        requests.append(
            TeamRevisionRequest(
                requested_by=note.author_role,
                target_role=note.target_role,
                instructions=f"{note.message} Suggested fix: {note.suggested_fix}",
                related_notes=(note.note_id,),
                must_fix_before_final=note.severity
                in {TeamReviewSeverity.MAJOR, TeamReviewSeverity.CRITICAL},
            )
        )
    return tuple(requests)


def review_decision_from_qa_result(
    content: str,
    *,
    existing_notes: Sequence[TeamReviewNote] = (),
) -> tuple[TeamReviewDecision, TeamReviewNote | None, TeamRevisionRequest | None]:
    normalized = _normalize_text(content).lower()
    needs_revision = any(
        marker in normalized
        for marker in (
            "needs_revision=true",
            "needs revision",
            "revision required",
            "нужна доработка",
            "нужно доработать",
            "не принято",
        )
    )
    approved = not needs_revision
    summary = _first_useful_line(content) or (
        "QA принял результат." if approved else "QA запросил доработку."
    )
    if approved:
        return (
            TeamReviewDecision(
                approved=True,
                needs_revision=False,
                blocking_notes=(),
                summary=summary,
            ),
            None,
            None,
        )

    qa_note = TeamReviewNote(
        author_role=AgentRole.QA_CONTROLLER.value,
        severity=TeamReviewSeverity.MAJOR,
        target_role=AgentRole.EDITOR.value,
        message=summary,
        suggested_fix="Внести одну дополнительную редакторскую правку и повторить QA.",
    )
    related_notes = tuple(note.note_id for note in existing_notes if note.severity != "info")
    if qa_note.note_id not in related_notes:
        related_notes = (*related_notes, qa_note.note_id)
    request = TeamRevisionRequest(
        requested_by=AgentRole.QA_CONTROLLER.value,
        target_role=AgentRole.EDITOR.value,
        instructions=summary,
        related_notes=related_notes,
        must_fix_before_final=True,
    )
    return (
        TeamReviewDecision(
            approved=False,
            needs_revision=True,
            blocking_notes=related_notes,
            summary=summary,
        ),
        qa_note,
        request,
    )


def build_final_package(
    *,
    final_text: str,
    brief: TeamTaskBrief | None,
    decision: TeamReviewDecision | None,
    applied_revision_count: int,
) -> TeamFinalPackage:
    limitations = []
    if decision is not None and decision.needs_revision:
        limitations.append("QA всё ещё просит доработку, но лимит revision loop исчерпан.")
    if brief is not None and brief.risk_notes:
        limitations.extend(brief.risk_notes)
    return TeamFinalPackage(
        final_text=final_text,
        brief_summary=brief.normalized_goal if brief is not None else "",
        applied_revision_count=applied_revision_count,
        remaining_limitations=tuple(limitations),
        quality_check_summary=decision.summary if decision is not None else "QA не выполнялся.",
    )


def task_brief_payload(brief: TeamTaskBrief | None) -> dict[str, Any] | None:
    if brief is None:
        return None
    return {
        "original_user_input": brief.original_user_input,
        "normalized_goal": brief.normalized_goal,
        "expected_output": brief.expected_output,
        "constraints": list(brief.constraints),
        "available_attachments": list(brief.available_attachments),
        "open_questions": list(brief.open_questions),
        "risk_notes": list(brief.risk_notes),
        "created_by": brief.created_by,
        "created_at": brief.created_at.isoformat(),
    }


def task_brief_from_payload(payload: dict[str, Any] | None) -> TeamTaskBrief | None:
    if payload is None:
        return None
    return TeamTaskBrief(
        original_user_input=payload["original_user_input"],
        normalized_goal=payload["normalized_goal"],
        expected_output=payload["expected_output"],
        constraints=tuple(payload.get("constraints", [])),
        available_attachments=tuple(payload.get("available_attachments", [])),
        open_questions=tuple(payload.get("open_questions", [])),
        risk_notes=tuple(payload.get("risk_notes", [])),
        created_by=payload["created_by"],
        created_at=datetime.fromisoformat(payload["created_at"]),
    )


def quality_criterion_payload(criterion: TeamQualityCriterion) -> dict[str, Any]:
    return {
        "criterion_id": criterion.criterion_id,
        "title": criterion.title,
        "description": criterion.description,
        "required": criterion.required,
        "source_agent": criterion.source_agent,
    }


def quality_criterion_from_payload(payload: dict[str, Any]) -> TeamQualityCriterion:
    return TeamQualityCriterion(
        criterion_id=payload["criterion_id"],
        title=payload["title"],
        description=payload["description"],
        required=payload["required"],
        source_agent=payload["source_agent"],
    )


def review_note_payload(note: TeamReviewNote) -> dict[str, Any]:
    return {
        "note_id": note.note_id,
        "author_role": note.author_role,
        "severity": note.severity.value,
        "target_role": note.target_role,
        "target_artifact": note.target_artifact,
        "message": note.message,
        "suggested_fix": note.suggested_fix,
    }


def review_note_from_payload(payload: dict[str, Any]) -> TeamReviewNote:
    return TeamReviewNote(
        note_id=payload["note_id"],
        author_role=payload["author_role"],
        severity=TeamReviewSeverity(payload["severity"]),
        target_role=payload.get("target_role"),
        target_artifact=payload.get("target_artifact"),
        message=payload["message"],
        suggested_fix=payload["suggested_fix"],
    )


def revision_request_payload(request: TeamRevisionRequest) -> dict[str, Any]:
    return {
        "requested_by": request.requested_by,
        "target_role": request.target_role,
        "instructions": request.instructions,
        "related_notes": list(request.related_notes),
        "must_fix_before_final": request.must_fix_before_final,
    }


def revision_request_from_payload(payload: dict[str, Any]) -> TeamRevisionRequest:
    return TeamRevisionRequest(
        requested_by=payload["requested_by"],
        target_role=payload["target_role"],
        instructions=payload["instructions"],
        related_notes=tuple(payload.get("related_notes", [])),
        must_fix_before_final=payload["must_fix_before_final"],
    )


def review_decision_payload(decision: TeamReviewDecision | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    return {
        "approved": decision.approved,
        "needs_revision": decision.needs_revision,
        "blocking_notes": list(decision.blocking_notes),
        "summary": decision.summary,
    }


def review_decision_from_payload(payload: dict[str, Any] | None) -> TeamReviewDecision | None:
    if payload is None:
        return None
    return TeamReviewDecision(
        approved=payload["approved"],
        needs_revision=payload["needs_revision"],
        blocking_notes=tuple(payload.get("blocking_notes", [])),
        summary=payload["summary"],
    )


def final_package_payload(package: TeamFinalPackage | None) -> dict[str, Any] | None:
    if package is None:
        return None
    return {
        "final_text": package.final_text,
        "brief_summary": package.brief_summary,
        "applied_revision_count": package.applied_revision_count,
        "remaining_limitations": list(package.remaining_limitations),
        "quality_check_summary": package.quality_check_summary,
    }


def final_package_from_payload(payload: dict[str, Any] | None) -> TeamFinalPackage | None:
    if payload is None:
        return None
    return TeamFinalPackage(
        final_text=payload["final_text"],
        brief_summary=payload["brief_summary"],
        applied_revision_count=payload["applied_revision_count"],
        remaining_limitations=tuple(payload.get("remaining_limitations", [])),
        quality_check_summary=payload["quality_check_summary"],
    )


def review_protocol_markdown(run: Any) -> str:
    sections = ["# Team Review Protocol", ""]
    sections.extend(["## Task Brief", ""])
    if run.task_brief is None:
        sections.append("No task brief.")
    else:
        brief = run.task_brief
        sections.extend(
            [
                f"- Goal: {brief.normalized_goal}",
                f"- Expected output: {brief.expected_output}",
                f"- Created by: {brief.created_by}",
            ]
        )
        for constraint in brief.constraints:
            sections.append(f"- Constraint: {constraint}")

    sections.extend(["", "## Quality Criteria", ""])
    if not run.quality_criteria:
        sections.append("No quality criteria.")
    for criterion in run.quality_criteria:
        required = "required" if criterion.required else "optional"
        sections.append(f"- `{criterion.criterion_id}` {criterion.title} ({required})")

    sections.extend(["", "## Review Notes", ""])
    if not run.review_notes:
        sections.append("No review notes.")
    for note in run.review_notes:
        target = note.target_role or note.target_artifact or "general"
        sections.append(f"- `{note.note_id}` {note.severity.value} -> {target}: {note.message}")

    sections.extend(["", "## Revision Requests", ""])
    if not run.revision_requests:
        sections.append("No revision requests.")
    for request in run.revision_requests:
        marker = "must fix" if request.must_fix_before_final else "optional"
        sections.append(
            f"- {request.requested_by} -> {request.target_role} ({marker}): {request.instructions}"
        )

    sections.extend(["", "## Review Decision", ""])
    if run.review_decision is None:
        sections.append("No review decision.")
    else:
        decision = run.review_decision
        sections.extend(
            [
                f"- Approved: {decision.approved}",
                f"- Needs revision: {decision.needs_revision}",
                f"- Summary: {decision.summary}",
            ]
        )

    sections.extend(["", "## Final Package", ""])
    if run.final_package is None:
        sections.append("No final package.")
    else:
        package = run.final_package
        sections.extend(
            [
                f"- Brief summary: {package.brief_summary}",
                f"- Applied revisions: {package.applied_revision_count}",
                f"- Quality check: {package.quality_check_summary}",
            ]
        )
        for limitation in package.remaining_limitations:
            sections.append(f"- Limitation: {limitation}")

    sections.append("")
    return "\n".join(sections)


def _normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def _expected_output_for(goal: str, attachments: Sequence[TeamInputAttachment]) -> str:
    lower_goal = goal.lower()
    if "проверь" in lower_goal or "найди слаб" in lower_goal:
        return "Короткий разбор с найденными слабостями и конкретными правками."
    if "план" in lower_goal or "составь" in lower_goal:
        return "Структурированный план действий."
    if attachments:
        return "Итоговый ответ по задаче с учётом доступного содержимого файлов."
    return "Полезный финальный ответ на задачу пользователя."


def _first_useful_line(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip(" -\t")
        if stripped:
            return stripped
    return ""


def _severity_from_text(text: str) -> TeamReviewSeverity:
    lower = text.lower()
    if "critical" in lower or "критич" in lower:
        return TeamReviewSeverity.CRITICAL
    if "minor" in lower or "мелк" in lower:
        return TeamReviewSeverity.MINOR
    if "info" in lower or "информац" in lower:
        return TeamReviewSeverity.INFO
    return TeamReviewSeverity.MAJOR
