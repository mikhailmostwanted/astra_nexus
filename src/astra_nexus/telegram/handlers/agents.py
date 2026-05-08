from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from astra_nexus.services.agent_service import AgentService
from astra_nexus.telegram.presenters.agent_messages import render_agents

router = Router()


@router.message(Command("agents"))
async def handle_agents(message: Message, agent_service: AgentService) -> None:
    await message.answer(render_agents(agent_service.list_agents()))
