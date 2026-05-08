from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


@router.message(Command("start"))
async def handle_start(message: Message) -> None:
    await message.answer(
        "Astra Nexus готов принять задачу.\nКоманды: /task <текст>, /status <task_id>, /agents"
    )
