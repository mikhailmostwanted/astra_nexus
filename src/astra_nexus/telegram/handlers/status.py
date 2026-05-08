from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from astra_nexus.services.agent_service import AgentService
from astra_nexus.services.task_service import TaskService
from astra_nexus.telegram.presenters.agent_messages import render_agents
from astra_nexus.telegram.presenters.task_cards import render_task_status

router = Router()


@router.message(Command("status"))
async def handle_status(
    message: Message,
    command: CommandObject,
    task_service: TaskService,
) -> None:
    task_id = (command.args or "").strip()
    if not task_id:
        await message.answer("Использование: /status <task_id>")
        return

    task = task_service.get_task(task_id)
    if task is None:
        await message.answer("Задача не найдена.")
        return
    await message.answer(render_task_status(task))


@router.message(Command("agents"))
async def handle_agents(message: Message, agent_service: AgentService) -> None:
    await message.answer(render_agents(agent_service.list_agents()))
