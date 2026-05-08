from __future__ import annotations

import asyncio

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from astra_nexus.core.orchestrator import TaskOrchestrator
from astra_nexus.telegram.presenters.task_cards import render_task_result

router = Router()


@router.message(Command("task"))
async def handle_task(
    message: Message,
    command: CommandObject,
    orchestrator: TaskOrchestrator,
) -> None:
    prompt = (command.args or "").strip()
    if not prompt:
        await message.answer("Использование: /task <текст задачи>")
        return

    user_id = f"telegram:{message.from_user.id if message.from_user else 'unknown'}"
    await message.answer("Задача принята. Запускаю команду агентов.")
    result = await asyncio.to_thread(
        orchestrator.run_task,
        user_id=user_id,
        title=prompt[:80],
        prompt=prompt,
    )
    await message.answer(render_task_result(result))
