from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from astra_nexus.db.models import Agent
from astra_nexus.services.agent_service import AgentService

router = APIRouter(prefix="/api/agents", tags=["agents"])


class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: str
    name: str
    description: str
    is_active: bool
    created_at: datetime


@router.get("", response_model=list[AgentRead])
def list_agents(request: Request) -> list[Agent]:
    agent_service: AgentService = request.app.state.agent_service
    return agent_service.list_agents()
