from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class TeamAttachmentType(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"
    PDF = "pdf"
    DOCX = "docx"
    BINARY = "binary"
    UNKNOWN = "unknown"


class TeamAttachmentExtractionStatus(StrEnum):
    PENDING = "pending"
    EXTRACTED = "extracted"
    METADATA_ONLY = "metadata_only"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


class TeamAttachmentValidationError(ValueError):
    pass


@dataclass
class TeamInputAttachment:
    original_filename: str
    stored_filename: str
    content_type: str | None = None
    size_bytes: int = 0
    source: str = "local"
    local_path: Path | None = None
    attachment_type: TeamAttachmentType = TeamAttachmentType.UNKNOWN
    extracted_text: str | None = None
    extraction_status: TeamAttachmentExtractionStatus = TeamAttachmentExtractionStatus.PENDING
    extraction_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TeamAttachmentManifest:
    attachments: tuple[TeamInputAttachment, ...] = ()

    @property
    def count(self) -> int:
        return len(self.attachments)


class TeamAttachmentProcessor:
    def __init__(
        self,
        *,
        max_files: int = 5,
        max_bytes: int = 10 * 1024 * 1024,
        text_max_chars: int = 20000,
    ) -> None:
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.text_max_chars = text_max_chars

    def prepare_paths(
        self,
        paths: list[Path] | tuple[Path, ...],
        *,
        source: str,
    ) -> tuple[TeamInputAttachment, ...]:
        self._validate_count(len(paths))
        attachments = []
        for path in paths:
            path = Path(path)
            size_bytes = path.stat().st_size
            self._validate_size(path.name, size_bytes)
            attachments.append(
                TeamInputAttachment(
                    original_filename=path.name,
                    stored_filename=_safe_filename(path.name),
                    content_type=_content_type(path),
                    size_bytes=size_bytes,
                    source=source,
                    local_path=path,
                    attachment_type=_attachment_type(path),
                )
            )
        return self.process(attachments)

    def process(
        self,
        attachments: list[TeamInputAttachment] | tuple[TeamInputAttachment, ...],
    ) -> tuple[TeamInputAttachment, ...]:
        self._validate_count(len(attachments))
        for attachment in attachments:
            self._validate_size(attachment.original_filename, attachment.size_bytes)
            self._extract(attachment)
        return tuple(attachments)

    def _extract(self, attachment: TeamInputAttachment) -> None:
        if attachment.attachment_type == TeamAttachmentType.UNKNOWN and attachment.local_path:
            attachment.attachment_type = _attachment_type(attachment.local_path)
        if attachment.attachment_type not in {TeamAttachmentType.TEXT, TeamAttachmentType.MARKDOWN}:
            attachment.extraction_status = TeamAttachmentExtractionStatus.METADATA_ONLY
            attachment.extracted_text = None
            return

        if attachment.local_path is None:
            attachment.extraction_status = TeamAttachmentExtractionStatus.ERROR
            attachment.extraction_error = "local_path is missing"
            return

        try:
            text = attachment.local_path.read_text(encoding="utf-8")
        except Exception as exc:
            attachment.extraction_status = TeamAttachmentExtractionStatus.ERROR
            attachment.extraction_error = str(exc)
            attachment.extracted_text = None
            return

        if self.text_max_chars > 0 and len(text) > self.text_max_chars:
            marker = (
                f"\n\n[Текст файла сокращён до {self.text_max_chars} символов. "
                "Полный файл сохранён в workspace.]"
            )
            text = text[: self.text_max_chars].rstrip() + marker
        attachment.extracted_text = text
        attachment.extraction_status = TeamAttachmentExtractionStatus.EXTRACTED
        attachment.extraction_error = None

    def _validate_count(self, count: int) -> None:
        if count > self.max_files:
            raise TeamAttachmentValidationError(
                f"too many attachments: {count}; max allowed is {self.max_files}"
            )

    def _validate_size(self, filename: str, size_bytes: int) -> None:
        if size_bytes > self.max_bytes:
            raise TeamAttachmentValidationError(
                f"attachment {filename} is too large: {size_bytes}; max allowed is {self.max_bytes}"
            )


def save_attachments_to_workspace(
    attachments: list[TeamInputAttachment],
    *,
    run_path: Path,
) -> None:
    input_files_path = run_path / "input_files"
    input_files_path.mkdir(parents=True, exist_ok=True)
    used_names: set[str] = set()
    for index, attachment in enumerate(attachments, start=1):
        stored_filename = _unique_filename(
            attachment.stored_filename or _safe_filename(attachment.original_filename),
            used_names,
            index=index,
        )
        attachment.stored_filename = stored_filename
        destination = input_files_path / stored_filename
        if attachment.local_path is not None and attachment.local_path.exists():
            source = attachment.local_path
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
            attachment.local_path = destination


def attachment_payload(attachment: TeamInputAttachment) -> dict[str, Any]:
    return {
        "original_filename": attachment.original_filename,
        "stored_filename": attachment.stored_filename,
        "content_type": attachment.content_type,
        "size_bytes": attachment.size_bytes,
        "source": attachment.source,
        "local_path": str(attachment.local_path) if attachment.local_path else None,
        "attachment_type": attachment.attachment_type.value,
        "extracted_text": attachment.extracted_text,
        "extraction_status": attachment.extraction_status.value,
        "extraction_error": attachment.extraction_error,
        "metadata": attachment.metadata,
    }


def attachment_from_payload(payload: dict[str, Any]) -> TeamInputAttachment:
    return TeamInputAttachment(
        original_filename=payload["original_filename"],
        stored_filename=payload["stored_filename"],
        content_type=payload.get("content_type"),
        size_bytes=payload.get("size_bytes", 0),
        source=payload.get("source", "workspace"),
        local_path=Path(payload["local_path"]) if payload.get("local_path") else None,
        attachment_type=TeamAttachmentType(payload.get("attachment_type", "unknown")),
        extracted_text=payload.get("extracted_text"),
        extraction_status=TeamAttachmentExtractionStatus(
            payload.get("extraction_status", "metadata_only")
        ),
        extraction_error=payload.get("extraction_error"),
        metadata=payload.get("metadata", {}),
    )


def attachments_markdown(attachments: list[TeamInputAttachment]) -> str:
    sections = ["# Attachments", ""]
    if not attachments:
        sections.extend(["No attachments.", ""])
        return "\n".join(sections)

    for index, attachment in enumerate(attachments, start=1):
        sections.extend(
            [
                f"## {index}. {attachment.original_filename}",
                "",
                f"- Stored filename: `{attachment.stored_filename}`",
                f"- Content type: `{attachment.content_type or 'unknown'}`",
                f"- Size: `{attachment.size_bytes}` bytes",
                f"- Source: `{attachment.source}`",
                f"- Local path: `{attachment.local_path or ''}`",
                f"- Extraction status: `{attachment.extraction_status.value}`",
            ]
        )
        if attachment.extraction_error:
            sections.append(f"- Extraction error: `{attachment.extraction_error}`")
        if attachment.extracted_text:
            sections.extend(
                ["", "### Extracted text", "", "```text", attachment.extracted_text, "```"]
            )
        sections.append("")
    return "\n".join(sections).rstrip() + "\n"


def _attachment_type(path: Path) -> TeamAttachmentType:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return TeamAttachmentType.TEXT
    if suffix == ".md":
        return TeamAttachmentType.MARKDOWN
    if suffix == ".pdf":
        return TeamAttachmentType.PDF
    if suffix == ".docx":
        return TeamAttachmentType.DOCX
    if suffix:
        return TeamAttachmentType.BINARY
    return TeamAttachmentType.UNKNOWN


def _content_type(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return "text/plain"
    if suffix == ".md":
        return "text/markdown"
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return None


def _safe_filename(filename: str) -> str:
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in filename)
    safe = safe.strip("._")
    return safe or "attachment"


def _unique_filename(filename: str, used_names: set[str], *, index: int) -> str:
    candidate = filename
    if candidate not in used_names:
        used_names.add(candidate)
        return candidate
    path = Path(filename)
    suffix = path.suffix
    stem = path.stem or "attachment"
    while candidate in used_names:
        candidate = f"{stem}_{index}{suffix}"
        index += 1
    used_names.add(candidate)
    return candidate
