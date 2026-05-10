from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from astra_nexus.team.attachments import TeamInputAttachment
from astra_nexus.team.models import AgentRole, RunStatus, TeamRun, utc_now
from astra_nexus.team.review_protocol import review_protocol_markdown


class TeamArtifactType(StrEnum):
    REQUESTED_OUTPUT = "requested_output"
    FINAL_ANSWER = "final_answer"
    EXECUTIVE_SUMMARY = "executive_summary"
    CRITIC_REPORT = "critic_report"
    QA_REPORT = "qa_report"
    REVIEW_PROTOCOL = "review_protocol"
    SOURCE_FILES_SUMMARY = "source_files_summary"
    RUN_MANIFEST = "run_manifest"
    ARTIFACTS_INDEX = "artifacts_index"


class TeamArtifactFormat(StrEnum):
    MARKDOWN = "markdown"
    JSON = "json"
    TEXT = "text"
    DOCX = "docx"
    PDF = "pdf"


@dataclass
class TeamArtifact:
    artifact_type: TeamArtifactType
    format: TeamArtifactFormat
    title: str
    path: Path
    relative_path: str
    primary: bool = False
    size_bytes: int = 0
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)


def generate_output_artifacts(run: TeamRun, *, run_path: Path) -> list[TeamArtifact]:
    if run.status != RunStatus.COMPLETED:
        return []

    artifacts_dir = run_path / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[TeamArtifact] = []
    requested_artifact = _requested_output_artifact(
        run, artifacts_dir=artifacts_dir, run_path=run_path
    )
    if requested_artifact is not None:
        artifacts.append(requested_artifact)

    artifacts.append(
        _write_markdown_artifact(
            artifacts_dir=artifacts_dir,
            run_path=run_path,
            artifact_type=TeamArtifactType.FINAL_ANSWER,
            filename="final_answer.md",
            title="Финальный ответ",
            content=_final_answer_markdown(run),
            primary=True,
        )
    )
    artifacts.append(
        _write_markdown_artifact(
            artifacts_dir=artifacts_dir,
            run_path=run_path,
            artifact_type=TeamArtifactType.EXECUTIVE_SUMMARY,
            filename="executive_summary.md",
            title="Краткое резюме",
            content=_executive_summary_markdown(run),
        )
    )
    artifacts.append(
        _write_markdown_artifact(
            artifacts_dir=artifacts_dir,
            run_path=run_path,
            artifact_type=TeamArtifactType.CRITIC_REPORT,
            filename="critic_report.md",
            title="Отчёт критика",
            content=_critic_report_markdown(run),
        )
    )
    artifacts.append(
        _write_markdown_artifact(
            artifacts_dir=artifacts_dir,
            run_path=run_path,
            artifact_type=TeamArtifactType.QA_REPORT,
            filename="qa_report.md",
            title="QA-отчёт",
            content=_qa_report_markdown(run),
        )
    )
    artifacts.append(
        _write_markdown_artifact(
            artifacts_dir=artifacts_dir,
            run_path=run_path,
            artifact_type=TeamArtifactType.REVIEW_PROTOCOL,
            filename="review_protocol.md",
            title="Review protocol",
            content=review_protocol_markdown(run),
        )
    )
    artifacts.append(
        _write_markdown_artifact(
            artifacts_dir=artifacts_dir,
            run_path=run_path,
            artifact_type=TeamArtifactType.SOURCE_FILES_SUMMARY,
            filename="source_files_summary.md",
            title="Сводка исходных файлов",
            content=source_files_summary_markdown(run.attachments),
        )
    )

    manifest_path = artifacts_dir / "run_manifest.json"
    manifest_artifact = TeamArtifact(
        artifact_type=TeamArtifactType.RUN_MANIFEST,
        format=TeamArtifactFormat.JSON,
        title="Run manifest",
        path=manifest_path,
        relative_path=_relative_path(manifest_path, run_path),
    )
    artifacts.append(manifest_artifact)

    index_path = artifacts_dir / "index.md"
    index_artifact = TeamArtifact(
        artifact_type=TeamArtifactType.ARTIFACTS_INDEX,
        format=TeamArtifactFormat.MARKDOWN,
        title="Индекс артефактов",
        path=index_path,
        relative_path=_relative_path(index_path, run_path),
    )
    artifacts.append(index_artifact)

    manifest_path.write_text(
        json.dumps(
            _run_manifest_payload(run, run_path=run_path, artifacts=artifacts),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_artifact.size_bytes = manifest_path.stat().st_size
    index_path.write_text(_index_markdown(run, artifacts=artifacts), encoding="utf-8")
    index_artifact.size_bytes = index_path.stat().st_size
    manifest_path.write_text(
        json.dumps(
            _run_manifest_payload(run, run_path=run_path, artifacts=artifacts),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_artifact.size_bytes = manifest_path.stat().st_size
    return artifacts


def artifact_payload(artifact: TeamArtifact) -> dict[str, Any]:
    return {
        "artifact_type": artifact.artifact_type.value,
        "format": artifact.format.value,
        "title": artifact.title,
        "path": str(artifact.path),
        "relative_path": artifact.relative_path,
        "primary": artifact.primary,
        "size_bytes": artifact.size_bytes,
        "created_at": artifact.created_at.isoformat(),
        "metadata": artifact.metadata,
    }


def primary_artifact(artifacts: list[TeamArtifact]) -> TeamArtifact | None:
    for artifact in artifacts:
        if artifact.primary:
            return artifact
    return artifacts[0] if artifacts else None


def artifacts_index(artifacts: list[TeamArtifact]) -> TeamArtifact | None:
    for artifact in artifacts:
        if artifact.path.name == "index.md":
            return artifact
    return None


def requested_output_artifact(artifacts: list[TeamArtifact]) -> TeamArtifact | None:
    for artifact in artifacts:
        if artifact.artifact_type == TeamArtifactType.REQUESTED_OUTPUT:
            return artifact
    return None


def source_files_summary_markdown(attachments: list[TeamInputAttachment]) -> str:
    sections = ["# Сводка исходных файлов", ""]
    if not attachments:
        sections.extend(["Файлов нет.", ""])
        return "\n".join(sections)
    for attachment in attachments:
        sections.extend(
            [
                f"## {attachment.original_filename}",
                "",
                f"- filename: `{attachment.original_filename}`",
                f"- stored_filename: `{attachment.stored_filename}`",
                (
                    f"- extension/type: `{attachment.extension or ''}` / "
                    f"`{attachment.attachment_type.value}`"
                ),
                f"- size: `{attachment.size_bytes}` bytes",
                f"- extraction_status: `{attachment.extraction_status.value}`",
                f"- extracted_chars: `{attachment.extracted_chars}`",
                f"- prompt_chars: `{attachment.prompt_chars}`",
                f"- truncated: `{attachment.truncated}`",
            ]
        )
        if attachment.extraction_error:
            sections.append(f"- extraction_error: `{attachment.extraction_error}`")
        sections.append("")
    return "\n".join(sections).rstrip() + "\n"


def _write_markdown_artifact(
    *,
    artifacts_dir: Path,
    run_path: Path,
    artifact_type: TeamArtifactType,
    filename: str,
    title: str,
    content: str,
    primary: bool = False,
) -> TeamArtifact:
    path = artifacts_dir / filename
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
    return TeamArtifact(
        artifact_type=artifact_type,
        format=TeamArtifactFormat.MARKDOWN,
        title=title,
        path=path,
        relative_path=_relative_path(path, run_path),
        primary=primary,
        size_bytes=path.stat().st_size,
    )


def _requested_output_artifact(
    run: TeamRun,
    *,
    artifacts_dir: Path,
    run_path: Path,
) -> TeamArtifact | None:
    metadata = run.runtime_metadata
    if not metadata.get("output_requested_as_file"):
        return None
    output_format = str(metadata.get("requested_output_format") or "unknown").lower()
    if output_format not in {"md", "docx", "pdf", "txt"}:
        output_format = "txt"
    text = _plain_text_for_file(run.final_text or "")
    if output_format == "docx":
        return _write_docx_artifact(artifacts_dir=artifacts_dir, run_path=run_path, text=text)
    if output_format == "pdf":
        return _write_pdf_artifact(artifacts_dir=artifacts_dir, run_path=run_path, text=text)
    if output_format == "md":
        return _write_markdown_artifact(
            artifacts_dir=artifacts_dir,
            run_path=run_path,
            artifact_type=TeamArtifactType.REQUESTED_OUTPUT,
            filename="requested_output.md",
            title="Запрошенный файл",
            content=run.final_text or "",
        )
    path = artifacts_dir / "requested_output.txt"
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return TeamArtifact(
        artifact_type=TeamArtifactType.REQUESTED_OUTPUT,
        format=TeamArtifactFormat.TEXT,
        title="Запрошенный файл",
        path=path,
        relative_path=_relative_path(path, run_path),
        size_bytes=path.stat().st_size,
        metadata={"requested_output": True, "requested_output_format": "txt"},
    )


def _write_docx_artifact(*, artifacts_dir: Path, run_path: Path, text: str) -> TeamArtifact:
    from docx import Document

    path = artifacts_dir / "requested_output.docx"
    document = Document()
    for block in text.split("\n\n"):
        document.add_paragraph(block.strip())
    document.save(path)
    return TeamArtifact(
        artifact_type=TeamArtifactType.REQUESTED_OUTPUT,
        format=TeamArtifactFormat.DOCX,
        title="Запрошенный файл",
        path=path,
        relative_path=_relative_path(path, run_path),
        size_bytes=path.stat().st_size,
        metadata={"requested_output": True, "requested_output_format": "docx"},
    )


def _write_pdf_artifact(*, artifacts_dir: Path, run_path: Path, text: str) -> TeamArtifact:
    path = artifacts_dir / "requested_output.pdf"
    _write_simple_pdf(path, text)
    return TeamArtifact(
        artifact_type=TeamArtifactType.REQUESTED_OUTPUT,
        format=TeamArtifactFormat.PDF,
        title="Запрошенный файл",
        path=path,
        relative_path=_relative_path(path, run_path),
        size_bytes=path.stat().st_size,
        metadata={"requested_output": True, "requested_output_format": "pdf"},
    )


def _final_answer_markdown(run: TeamRun) -> str:
    return "\n".join(["# Финальный ответ", "", run.final_text or ""])


def _executive_summary_markdown(run: TeamRun) -> str:
    attachments = ", ".join(attachment.original_filename for attachment in run.attachments) or "нет"
    final_preview = _preview(run.final_text or "")
    limitations = (
        list(run.final_package.remaining_limitations) if run.final_package is not None else []
    )
    if not limitations and run.review_decision is not None and not run.review_decision.approved:
        limitations.append("QA не подтвердил полную готовность результата.")
    if not limitations:
        limitations.append("Явных ограничений не зафиксировано.")
    return "\n".join(
        [
            "# Краткое резюме",
            "",
            "## Что было на входе",
            "",
            f"- Задача: {run.user_task}",
            f"- Файлы: {attachments}",
            "",
            "## Что сделала команда",
            "",
            (
                "- Разобрала задачу по ролям: координатор, аналитик, критик, "
                "редактор, QA и финальная сборка."
            ),
            "- Проверила результат через critic/QA protocol, если эти этапы были выполнены.",
            "",
            "## Что получилось",
            "",
            final_preview or "Финальный текст не найден.",
            "",
            "## Ограничения и риски",
            "",
            *[f"- {limitation}" for limitation in limitations],
        ]
    )


def _critic_report_markdown(run: TeamRun) -> str:
    critic_result = _result_for_role(run, AgentRole.CRITIC)
    sections = ["# Отчёт критика", ""]
    sections.extend(
        ["## Результат critic", "", critic_result or "Критик не вернул отдельный результат."]
    )
    sections.extend(["", "## Review notes", ""])
    if not run.review_notes:
        sections.append("Замечаний не сохранено.")
    else:
        for note in run.review_notes:
            if note.author_role != AgentRole.CRITIC.value:
                continue
            sections.extend(
                [
                    f"- `{note.severity.value}` {note.message}",
                    f"  Исправление: {note.suggested_fix}",
                ]
            )
    return "\n".join(sections)


def _qa_report_markdown(run: TeamRun) -> str:
    qa_result = _result_for_role(run, AgentRole.QA_CONTROLLER)
    sections = ["# QA-отчёт", ""]
    sections.extend(["## Результат QA", "", qa_result or "QA не вернул отдельный результат."])
    sections.extend(["", "## Решение QA", ""])
    if run.review_decision is None:
        sections.append("Решение QA не сохранено.")
    else:
        blocking = ", ".join(run.review_decision.blocking_notes) or "нет"
        sections.extend(
            [
                f"- approved: `{run.review_decision.approved}`",
                f"- needs_revision: `{run.review_decision.needs_revision}`",
                f"- blocking_notes: `{blocking}`",
                f"- summary: {run.review_decision.summary}",
            ]
        )
    return "\n".join(sections)


def _index_markdown(run: TeamRun, *, artifacts: list[TeamArtifact]) -> str:
    input_files = [attachment.original_filename for attachment in run.attachments]
    sections = [
        "# Индекс итоговых артефактов",
        "",
        f"- run_id: `{run.id}`",
        f"- status: `{run.status.value}`",
        f"- timestamp: `{utc_now().isoformat()}`",
        "",
        "## Задача пользователя",
        "",
        run.user_task,
        "",
        "## Входные файлы",
        "",
    ]
    if input_files:
        sections.extend(f"- {filename}" for filename in input_files)
    else:
        sections.append("- Файлов нет.")
    sections.extend(["", "## Созданные артефакты", ""])
    for artifact in artifacts:
        sections.append(
            f"- `{artifact.artifact_type.value}` / `{artifact.format.value}`: "
            f"{artifact.relative_path}"
        )
    return "\n".join(sections) + "\n"


def _run_manifest_payload(
    run: TeamRun,
    *,
    run_path: Path,
    artifacts: list[TeamArtifact],
) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "status": run.status.value,
        "user_task": run.user_task,
        "workspace_path": str(run_path),
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.completed_at.isoformat() if run.completed_at else None,
        "input_files": [
            {
                "filename": attachment.original_filename,
                "stored_filename": attachment.stored_filename,
                "type": attachment.attachment_type.value,
                "extension": attachment.extension,
                "size_bytes": attachment.size_bytes,
                "extraction_status": attachment.extraction_status.value,
                "extracted_chars": attachment.extracted_chars,
                "prompt_chars": attachment.prompt_chars,
                "truncated": attachment.truncated,
                "extraction_error": attachment.extraction_error,
            }
            for attachment in run.attachments
        ],
        "artifacts": [artifact_payload(artifact) for artifact in artifacts],
    }


def _plain_text_for_file(value: str) -> str:
    text = str(value or "")
    if "<" in text and ">" in text:
        parser = _HTMLTextExtractor()
        try:
            parser.feed(text)
            extracted = parser.text()
            if extracted:
                return extracted
        except Exception:
            return text
    return text


class _HTMLTextExtractor(HTMLParser):
    block_tags = {"p", "div", "section", "article", "li", "blockquote", "pre"}
    heading_tags = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.block_tags or tag in self.heading_tags:
            self._break()
        if tag == "li":
            self.parts.append("- ")
        if tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.block_tags or tag in self.heading_tags:
            self._break()

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def text(self) -> str:
        text = "".join(self.parts).replace("\r\n", "\n")
        lines = [" ".join(line.split()) for line in text.splitlines()]
        return "\n".join(lines).strip()

    def _break(self) -> None:
        current = "".join(self.parts)
        if current and not current.endswith("\n\n"):
            self.parts.append("\n\n")


def _write_simple_pdf(path: Path, text: str) -> None:
    lines = []
    for paragraph in text.splitlines() or [""]:
        wrapped = textwrap.wrap(paragraph, width=88) or [""]
        lines.extend(wrapped)
    lines = lines[:700]
    content_lines = ["BT", "/F1 10 Tf", "50 780 Td", "14 TL"]
    for index, line in enumerate(lines):
        if index:
            content_lines.append("T*")
        content_lines.append(f"({_escape_pdf_text(line)}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("utf-8")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode("ascii"))
        output.extend(payload)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    path.write_bytes(bytes(output))


def _escape_pdf_text(value: str) -> str:
    return (
        value.encode("latin-1", errors="replace")
        .decode("latin-1")
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )


def _result_for_role(run: TeamRun, role: AgentRole) -> str | None:
    for result in reversed(run.results):
        if result.profile.role == role:
            return result.content
    return None


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _preview(text: str, *, limit: int = 700) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1].rstrip()}..."
