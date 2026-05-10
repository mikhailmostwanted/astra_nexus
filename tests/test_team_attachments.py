from __future__ import annotations

import asyncio
import json
import zipfile
from textwrap import dedent

import pytest

from astra_nexus.config.settings import Settings
from astra_nexus.team import (
    AgentRole,
    AsyncTeamOrchestrator,
    FakeTeamProvider,
    TeamAttachmentExtractionStatus,
    TeamAttachmentProcessor,
    TeamAttachmentValidationError,
    TeamInputAttachment,
    TeamRunWorkspace,
)
from astra_nexus.team.attachments import attachment_from_payload


def test_attachment_extraction_settings_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.team_attachment_max_extracted_chars == 50000
    assert settings.team_attachment_max_prompt_chars == 20000
    assert settings.team_attachment_pdf_max_pages == 30
    assert settings.team_attachment_docx_include_tables is True


def test_txt_and_md_attachments_are_extracted_and_added_to_prompt(tmp_path) -> None:
    txt_path = tmp_path / "notes.txt"
    md_path = tmp_path / "brief.md"
    txt_path.write_text("TXT контекст для команды.", encoding="utf-8")
    md_path.write_text("# План\n\nНужно улучшить AI Team docs.", encoding="utf-8")
    processor = TeamAttachmentProcessor(max_extracted_chars=2000, max_prompt_chars=2000)
    attachments = processor.prepare_paths([txt_path, md_path], source="test")
    provider = FakeTeamProvider()
    orchestrator = AsyncTeamOrchestrator(provider=provider)

    asyncio.run(orchestrator.run("проверь файлы", attachments=attachments))

    assert [attachment.extraction_status for attachment in attachments] == [
        TeamAttachmentExtractionStatus.EXTRACTED,
        TeamAttachmentExtractionStatus.EXTRACTED,
    ]
    assert attachments[0].extracted_chars == len("TXT контекст для команды.")
    assert attachments[1].extension == ".md"
    assert attachments[1].prompt_chars == attachments[1].extracted_chars
    first_prompt = provider.calls[0].prompt.user_prompt
    assert "TXT контекст для команды." in first_prompt
    assert "Нужно улучшить AI Team docs." in first_prompt


def test_docx_attachment_extracts_paragraphs_and_prompt_text(tmp_path) -> None:
    file_path = tmp_path / "strategy.docx"
    _write_docx(file_path, paragraphs=["Первый абзац DOCX.", "Второй абзац для critic."])
    attachments = TeamAttachmentProcessor().prepare_paths([file_path], source="test")
    provider = FakeTeamProvider()

    asyncio.run(
        AsyncTeamOrchestrator(provider=provider).run("проверь docx", attachments=attachments)
    )

    attachment = attachments[0]
    assert attachment.extraction_status == TeamAttachmentExtractionStatus.EXTRACTED
    assert attachment.attachment_type.value == "docx"
    assert attachment.paragraphs_count == 2
    assert "Первый абзац DOCX." in (attachment.extracted_text or "")
    assert "Второй абзац для critic." in provider.calls[0].prompt.user_prompt


def test_docx_attachment_extracts_tables_when_enabled(tmp_path) -> None:
    file_path = tmp_path / "table.docx"
    _write_docx(
        file_path,
        paragraphs=["Перед таблицей."],
        table_rows=[["Риск", "Критичность"], ["Пустой критерий", "Высокая"]],
    )

    attachments = TeamAttachmentProcessor(docx_include_tables=True).prepare_paths(
        [file_path],
        source="test",
    )

    assert attachments[0].extraction_status == TeamAttachmentExtractionStatus.EXTRACTED
    assert "Риск | Критичность" in (attachments[0].extracted_text or "")
    assert "Пустой критерий | Высокая" in (attachments[0].extracted_text or "")


def test_pdf_attachment_extracts_pages_with_page_numbers(tmp_path) -> None:
    file_path = tmp_path / "simple.pdf"
    _write_simple_pdf(file_path, ["PDF text first page", "PDF text second page"])

    attachments = TeamAttachmentProcessor(pdf_max_pages=30).prepare_paths(
        [file_path], source="test"
    )

    attachment = attachments[0]
    assert attachment.extraction_status == TeamAttachmentExtractionStatus.EXTRACTED
    assert attachment.pages_count == 2
    assert "Страница 1" in (attachment.extracted_text or "")
    assert "PDF text first page" in (attachment.extracted_text or "")
    assert "Страница 2" in (attachment.extracted_text or "")
    assert "PDF text second page" in (attachment.extracted_text or "")


def test_oversized_extraction_is_truncated_for_prompt(tmp_path) -> None:
    file_path = tmp_path / "notes.md"
    file_path.write_text("А" * 120, encoding="utf-8")
    processor = TeamAttachmentProcessor(max_extracted_chars=80, max_prompt_chars=25)
    attachments = processor.prepare_paths([file_path], source="test")
    provider = FakeTeamProvider()

    asyncio.run(
        AsyncTeamOrchestrator(provider=provider).run("проверь файл", attachments=attachments)
    )

    assert attachments[0].extraction_status == TeamAttachmentExtractionStatus.TRUNCATED
    assert attachments[0].truncated is True
    assert attachments[0].extracted_chars == 80
    assert attachments[0].prompt_chars == 25
    assert len(attachments[0].extracted_text or "") == 80
    first_prompt = provider.calls[0].prompt.user_prompt
    assert "Текст файла обрезан для prompt" in first_prompt
    assert "А" * 25 in first_prompt
    assert "А" * 40 not in first_prompt


def test_unsupported_attachment_is_metadata_only_and_does_not_fail_run(tmp_path) -> None:
    file_path = tmp_path / "payload.bin"
    file_path.write_bytes(b"\x00\x01\x02")
    attachments = TeamAttachmentProcessor().prepare_paths([file_path], source="test")
    provider = FakeTeamProvider()

    outcome = asyncio.run(
        AsyncTeamOrchestrator(provider=provider).run("проверь файл", attachments=attachments)
    )

    assert outcome.run.status.value == "completed"
    assert attachments[0].extraction_status == TeamAttachmentExtractionStatus.METADATA_ONLY
    assert "Текст не извлечён" in provider.calls[0].prompt.user_prompt


def test_workspace_saves_attachments_manifest_and_input_files(tmp_path) -> None:
    file_path = tmp_path / "brief.txt"
    file_path.write_text("Текст брифа", encoding="utf-8")
    attachments = TeamAttachmentProcessor().prepare_paths([file_path], source="test")
    outcome = asyncio.run(
        AsyncTeamOrchestrator(provider=FakeTeamProvider()).run(
            "проверь файл",
            attachments=attachments,
        )
    )
    workspace = TeamRunWorkspace(root_path=tmp_path / "team_runs")

    run_path = workspace.save(outcome.run)

    attachments_payload = json.loads((run_path / "attachments.json").read_text(encoding="utf-8"))
    attachment_payload = attachments_payload["attachments"][0]
    assert attachment_payload["original_filename"] == "brief.txt"
    assert attachment_payload["extraction_status"] == "extracted"
    assert attachment_payload["extension"] == ".txt"
    assert attachment_payload["mime_type"] == "text/plain"
    assert attachment_payload["original_size"] == len("Текст брифа".encode())
    assert attachment_payload["extracted_chars"] == len("Текст брифа")
    assert attachment_payload["prompt_chars"] == len("Текст брифа")
    attachments_md = (run_path / "attachments.md").read_text(encoding="utf-8")
    assert "Extraction status: `extracted`" in attachments_md
    assert "Extracted chars:" in attachments_md
    assert (run_path / "input_files" / "brief.txt").read_text(encoding="utf-8") == "Текст брифа"


def test_legacy_attachment_payload_with_extracted_text_remains_prompt_readable() -> None:
    attachment = attachment_from_payload(
        {
            "original_filename": "legacy.md",
            "stored_filename": "legacy.md",
            "content_type": "text/markdown",
            "size_bytes": 10,
            "source": "workspace",
            "local_path": None,
            "attachment_type": "markdown",
            "extracted_text": "Старый извлечённый текст",
            "extraction_status": "extracted",
            "extraction_error": None,
            "metadata": {},
        }
    )

    assert attachment.extraction_status == TeamAttachmentExtractionStatus.EXTRACTED
    assert attachment.extracted_chars == len("Старый извлечённый текст")
    assert attachment.prompt_chars == len("Старый извлечённый текст")
    assert attachment.prompt_text == "Старый извлечённый текст"


def test_attachment_limits_are_enforced(tmp_path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")

    with pytest.raises(TeamAttachmentValidationError):
        TeamAttachmentProcessor(max_files=1).prepare_paths([first, second], source="test")

    with pytest.raises(TeamAttachmentValidationError):
        TeamAttachmentProcessor(max_bytes=2).prepare_paths([first], source="test")


def test_extractor_error_is_captured_and_does_not_fail_pipeline(tmp_path) -> None:
    missing_path = tmp_path / "missing.md"
    attachment = TeamInputAttachment(
        original_filename="missing.md",
        stored_filename="missing.md",
        content_type="text/markdown",
        size_bytes=0,
        source="test",
        local_path=missing_path,
    )
    attachments = TeamAttachmentProcessor().process([attachment])
    provider = FakeTeamProvider()

    outcome = asyncio.run(
        AsyncTeamOrchestrator(provider=provider).run("проверь файл", attachments=attachments)
    )

    assert outcome.run.status.value == "completed"
    assert attachments[0].extraction_status == TeamAttachmentExtractionStatus.FAILED
    assert attachments[0].extraction_error
    assert "Ошибка извлечения текста" in provider.calls[0].prompt.user_prompt


def test_corrupted_docx_and_pdf_are_failed_without_breaking_pipeline(tmp_path) -> None:
    docx_path = tmp_path / "broken.docx"
    pdf_path = tmp_path / "broken.pdf"
    docx_path.write_bytes(b"not a docx")
    pdf_path.write_bytes(b"%PDF broken")
    attachments = TeamAttachmentProcessor().prepare_paths([docx_path, pdf_path], source="test")
    provider = FakeTeamProvider()

    outcome = asyncio.run(
        AsyncTeamOrchestrator(provider=provider).run("проверь файлы", attachments=attachments)
    )

    assert outcome.run.status.value == "completed"
    assert [attachment.extraction_status for attachment in attachments] == [
        TeamAttachmentExtractionStatus.FAILED,
        TeamAttachmentExtractionStatus.FAILED,
    ]
    assert all(attachment.extraction_error for attachment in attachments)
    assert "Ошибка извлечения текста" in provider.calls[0].prompt.user_prompt


def test_final_composer_receives_file_context(tmp_path) -> None:
    file_path = tmp_path / "final.txt"
    file_path.write_text("Финальный контекст файла", encoding="utf-8")
    attachments = TeamAttachmentProcessor().prepare_paths([file_path], source="test")
    provider = FakeTeamProvider()

    asyncio.run(
        AsyncTeamOrchestrator(provider=provider).run("собери ответ", attachments=attachments)
    )

    final_call = provider.calls[-1]
    assert final_call.profile.role == AgentRole.FINAL_COMPOSER
    assert "Финальный контекст файла" in final_call.prompt.user_prompt


def _write_docx(
    path,
    *,
    paragraphs: list[str],
    table_rows: list[list[str]] | None = None,
) -> None:
    paragraph_xml = "\n".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    table_xml = ""
    if table_rows:
        rows = []
        for row in table_rows:
            cells = "".join(f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>" for cell in row)
            rows.append(f"<w:tr>{cells}</w:tr>")
        table_xml = f"<w:tbl>{''.join(rows)}</w:tbl>"
    document_xml = dedent(
        f"""\
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body>
            {paragraph_xml}
            {table_xml}
            <w:sectPr/>
          </w:body>
        </w:document>
        """
    ).lstrip()
    with zipfile.ZipFile(path, "w") as archive:
        relationships_content_type = "application/vnd.openxmlformats-package.relationships+xml"
        document_content_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
        )
        archive.writestr(
            "[Content_Types].xml",
            "\n".join(
                [
                    '<?xml version="1.0" encoding="UTF-8"?>',
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
                    f'  <Default Extension="rels" ContentType="{relationships_content_type}"/>',
                    '  <Default Extension="xml" ContentType="application/xml"/>',
                    (
                        '  <Override PartName="/word/document.xml" '
                        f'ContentType="{document_content_type}"/>'
                    ),
                    "</Types>",
                    "",
                ]
            ),
        )
        office_document_type = (
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
        )
        archive.writestr(
            "_rels/.rels",
            "\n".join(
                [
                    '<?xml version="1.0" encoding="UTF-8"?>',
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
                    (
                        f'  <Relationship Id="rId1" Type="{office_document_type}" '
                        'Target="word/document.xml"/>'
                    ),
                    "</Relationships>",
                    "",
                ]
            ),
        )
        archive.writestr("word/document.xml", document_xml)


def _write_simple_pdf(path, pages: list[str]) -> None:
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + b" ".join(f"{3 + index * 2} 0 R".encode("ascii") for index in range(len(pages)))
        + f"] /Count {len(pages)} >>".encode("ascii"),
    ]
    for index, text in enumerate(pages):
        page_object_id = 3 + index * 2
        stream_object_id = page_object_id + 1
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 18 Tf 72 720 Td ({escaped}) Tj ET".encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {len(pages) * 2 + 3} 0 R >> >> "
            f"/Contents {stream_object_id} 0 R >>".encode("ascii")
        )
        objects.append(
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    content = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id, payload in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{object_id} 0 obj\n".encode("ascii"))
        content.extend(payload)
        content.extend(b"\nendobj\n")
    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(bytes(content))
