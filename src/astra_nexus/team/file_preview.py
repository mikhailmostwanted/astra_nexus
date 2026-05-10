from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from astra_nexus.config.settings import load_settings
from astra_nexus.team.attachments import TeamAttachmentProcessor
from astra_nexus.team.telegram_bridge import (
    RecordingTelegramBot,
    TelegramTeamBridge,
    TelegramTeamBridgeConfig,
    _print_new_messages,
    _wait_for_preview_job,
)

DEFAULT_FILE_TASK = "Проверь приложенные файлы."


async def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = load_settings()
    processor = TeamAttachmentProcessor(
        max_files=settings.team_attachments_max_files,
        max_bytes=settings.team_attachment_max_bytes,
        text_max_chars=settings.team_attachment_text_max_chars,
    )
    attachments = processor.prepare_paths(tuple(args.files), source="file_preview")
    message = " ".join(args.message).strip() or DEFAULT_FILE_TASK
    config = TelegramTeamBridgeConfig(
        provider="fake",
        workspace_root=args.workspace_root or settings.team_runs_dir,
        uploads_root=settings.team_uploads_dir,
        attachment_max_files=settings.team_attachments_max_files,
        attachment_max_bytes=settings.team_attachment_max_bytes,
        attachment_text_max_chars=settings.team_attachment_text_max_chars,
    )
    bot = RecordingTelegramBot()
    bridge = TelegramTeamBridge(bot=bot, config=config)

    for attachment in attachments:
        print(f"[Файл] {attachment.original_filename}: {attachment.extraction_status.value}")
        if attachment.extracted_text:
            print(attachment.extracted_text)
    await bridge.handle_text(chat_id=args.chat_id, text=message, attachments=attachments)
    await _wait_for_preview_job(bridge=bridge, chat_id=args.chat_id)
    _print_new_messages(bot.messages, 0)
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview AI Team file task flow.")
    parser.add_argument("--file", action="append", dest="files", required=True, type=Path)
    parser.add_argument("message", nargs="*", help="Текст задачи по файлам.")
    parser.add_argument("--chat-id", type=int, default=100)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Папка для team run workspaces.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
