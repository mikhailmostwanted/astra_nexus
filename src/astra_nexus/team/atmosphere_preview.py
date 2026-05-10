from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path

from astra_nexus.config.settings import load_settings
from astra_nexus.team.messages import TeamMessageChannel
from astra_nexus.team.telegram_bridge import (
    RecordingTelegramBot,
    TelegramOutgoingMessage,
    TelegramTeamBridge,
    TelegramTeamBridgeConfig,
    _wait_for_preview_job,
)


async def run_preview(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = load_settings()
    config = TelegramTeamBridgeConfig.from_settings(settings)
    config = TelegramTeamBridgeConfig(
        provider="fake",
        workspace_root=args.workspace_root or config.workspace_root,
        uploads_root=config.uploads_root,
        log_chat_id=args.log_chat_id,
        send_typing=False,
        atmosphere=config.atmosphere,
    )
    bot = RecordingTelegramBot()
    bridge = TelegramTeamBridge(bot=bot, config=config)

    with tempfile.TemporaryDirectory(prefix="astra-nexus-atmosphere-preview-") as temp_dir:
        temp_path = Path(temp_dir)
        file_without_caption = temp_path / "context.md"
        file_without_caption.write_text("Контекст без подписи.", encoding="utf-8")
        file_with_caption = temp_path / "brief.md"
        file_with_caption.write_text("Контекст для задачи из файла.", encoding="utf-8")

        scenarios = (
            ("casual text", "брат че думаешь", ()),
            ("new task", "сделай краткий план AI-команды", ()),
            (
                "file without caption",
                "",
                bridge.attachment_processor.prepare_paths(
                    [file_without_caption],
                    source="atmosphere_preview",
                ),
            ),
            (
                "file with caption",
                "проверь файл и сделай краткий вывод",
                bridge.attachment_processor.prepare_paths(
                    [file_with_caption],
                    source="atmosphere_preview",
                ),
            ),
            ("/status", "/status", ()),
            ("/stopall", "/stopall", ()),
        )

        for label, text, attachments in scenarios:
            print(f"> {label}")
            await bridge.handle_text(chat_id=args.chat_id, text=text, attachments=attachments)
            await _wait_for_preview_job(bridge=bridge, chat_id=args.chat_id)

    _print_channel("MAIN CHAT", bot.messages, TeamMessageChannel.MAIN_CHAT)
    _print_channel("LOG CHAT", bot.messages, TeamMessageChannel.LOG_CHAT)
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run_preview(argv))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview Telegram Team Atmosphere without Telegram API."
    )
    parser.add_argument("--chat-id", type=int, default=100)
    parser.add_argument("--log-chat-id", type=int, default=200)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Папка для team run workspaces.",
    )
    return parser.parse_args(argv)


def _print_channel(
    title: str,
    messages: list[TelegramOutgoingMessage],
    channel: TeamMessageChannel,
) -> None:
    print(title)
    visible = [message for message in messages if message.channel == channel]
    if not visible:
        print("(empty)")
        return
    for message in visible:
        print(message.text)


if __name__ == "__main__":
    raise SystemExit(main())
