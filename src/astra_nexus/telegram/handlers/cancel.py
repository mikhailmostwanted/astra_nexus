from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from astra_nexus.core.task_state import TaskState
from astra_nexus.services.task_service import TaskService
from astra_nexus.telegram.presenters.task_cards import render_task_cancelled

router = Router()


@router.message(Command("cancel"))
async def handle_cancel(
    message: Message,
    command: CommandObject,
    task_service: TaskService,
) -> None:
    task_id = (command.args or "").strip()
    if not task_id:
        await message.answer("Использование: /cancel <task_id>")
        return

    task = task_service.get_task(task_id)
    if task is None:
        await message.answer("Задача не найдена.")
        return
    if task.state == TaskState.DONE.value:
        await message.answer(f"Astra Nexus\nЗадача уже завершена: {task.id}")
        return

    cancelled = task_service.cancel_task(task_id)
    await message.answer(render_task_cancelled(cancelled))
