from __future__ import annotations

import asyncio
import json

import pytest

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


def test_txt_and_md_attachment_is_extracted_and_added_to_prompt(tmp_path) -> None:
    file_path = tmp_path / "notes.md"
    file_path.write_text("# План\n\nНужно улучшить AI Team docs.", encoding="utf-8")
    processor = TeamAttachmentProcessor(text_max_chars=2000)
    attachments = processor.prepare_paths([file_path], source="test")
    provider = FakeTeamProvider()
    orchestrator = AsyncTeamOrchestrator(provider=provider)

    asyncio.run(orchestrator.run("проверь файл", attachments=attachments))

    assert attachments[0].extraction_status == TeamAttachmentExtractionStatus.EXTRACTED
    first_prompt = provider.calls[0].prompt.user_prompt
    assert "Файлы пользователя" in first_prompt
    assert "notes.md" in first_prompt
    assert "Нужно улучшить AI Team docs." in first_prompt


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
    assert attachments_payload["attachments"][0]["original_filename"] == "brief.txt"
    assert attachments_payload["attachments"][0]["extraction_status"] == "extracted"
    assert (run_path / "attachments.md").exists()
    assert (run_path / "input_files" / "brief.txt").read_text(encoding="utf-8") == "Текст брифа"


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
    assert attachments[0].extraction_status == TeamAttachmentExtractionStatus.ERROR
    assert attachments[0].extraction_error
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
