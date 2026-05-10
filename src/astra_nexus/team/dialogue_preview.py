from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from astra_nexus.config.settings import load_settings
from astra_nexus.team.attachments import TeamAttachmentProcessor
from astra_nexus.team.fake_provider import FakeTeamProvider
from astra_nexus.team.orchestrator import AsyncTeamOrchestrator

DEFAULT_DIALOGUE_TASK = "Проверь идею AI-команды."


async def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = load_settings()
    user_task = " ".join(args.task).strip() or DEFAULT_DIALOGUE_TASK
    attachments = ()
    if args.files:
        attachments = TeamAttachmentProcessor(
            max_files=settings.team_attachments_max_files,
            max_bytes=settings.team_attachment_max_bytes,
            max_extracted_chars=settings.team_attachment_max_extracted_chars,
            max_prompt_chars=settings.team_attachment_max_prompt_chars,
            pdf_max_pages=settings.team_attachment_pdf_max_pages,
            docx_include_tables=settings.team_attachment_docx_include_tables,
        ).prepare_paths(tuple(args.files), source="dialogue_preview")

    outcome = await AsyncTeamOrchestrator(provider=FakeTeamProvider()).run(
        user_task,
        attachments=attachments,
    )

    for turn in outcome.run.dialogue_turns:
        if turn.is_user_visible:
            print(f"[{_short_name(turn.agent_display_name)}] {turn.text}")
    print("")
    print(outcome.final_text)
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview AI Team dialogue with fake provider.")
    parser.add_argument("--file", action="append", dest="files", type=Path)
    parser.add_argument(
        "task",
        nargs="*",
        help="Текст задачи. Если не указан, используется дефолтная задача.",
    )
    return parser.parse_args(argv)


def _short_name(display_name: str) -> str:
    return display_name.split("/", maxsplit=1)[0].strip() or display_name


if __name__ == "__main__":
    raise SystemExit(main())
