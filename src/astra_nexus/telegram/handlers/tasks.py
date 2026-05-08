from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from astra_nexus.telegram.task_runner import TelegramTaskRunner

router = Router()


@router.message(Command("task"))
async def handle_task(
    message: Message,
    command: CommandObject,
    task_runner: TelegramTaskRunner,
) -> None:
    prompt = (command.args or "").strip()
    if not prompt:
        await message.answer("Использование: /task <текст задачи>")
        return

    user_id = f"telegram:{message.from_user.id if message.from_user else 'unknown'}"
    await task_runner.start(
        message=message,
        user_id=user_id,
        title=prompt[:80],
        prompt=prompt,
    )
