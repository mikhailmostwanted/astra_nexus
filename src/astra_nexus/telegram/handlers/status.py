from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from astra_nexus.config.settings import Settings
from astra_nexus.services.message_service import MessageService
from astra_nexus.services.task_service import TaskService
from astra_nexus.telegram.presenters.task_cards import render_task_status

router = Router()


@router.message(Command("status"))
async def handle_status(
    message: Message,
    command: CommandObject,
    task_service: TaskService,
    message_service: MessageService,
    settings: Settings,
) -> None:
    task_id = (command.args or "").strip()
    if not task_id:
        await message.answer("Использование: /status <task_id>")
        return

    task = task_service.get_task(task_id)
    if task is None:
        await message.answer("Задача не найдена.")
        return
    messages = message_service.list_for_task(task_id, limit=5)
    final_text = messages[-1].content if task.state == "done" and messages else None
    await message.answer(
        render_task_status(
            task,
            recent_messages=messages,
            workspace_path=settings.workspace_base_path / task_id,
            final_text=final_text,
        )
    )
