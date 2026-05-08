from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


@router.message(Command("start"))
async def handle_start(message: Message) -> None:
    await message.answer(
        "Astra Nexus\n"
        "Личный командный центр AI-агентов в Telegram.\n\n"
        "Команды:\n"
        "/task <текст> - поставить задачу\n"
        "/status <task_id> - посмотреть состояние\n"
        "/cancel <task_id> - отменить задачу\n"
        "/agents - список агентов"
    )
