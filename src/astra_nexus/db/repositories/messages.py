from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from astra_nexus.db.models import AgentMessage
from astra_nexus.utils.ids import new_id


class MessageRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        task_id: str,
        run_id: str,
        agent_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentMessage:
        message = AgentMessage(
            id=new_id("msg"),
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
            role=role,
            content=content,
            metadata_json=metadata or {},
        )
        self.session.add(message)
        return message

    def list_for_run(self, run_id: str) -> list[AgentMessage]:
        stmt = (
            select(AgentMessage)
            .where(AgentMessage.run_id == run_id)
            .order_by(AgentMessage.created_at)
        )
        return list(self.session.scalars(stmt))

    def list_for_task(self, task_id: str, limit: int = 10) -> list[AgentMessage]:
        stmt = (
            select(AgentMessage)
            .where(AgentMessage.task_id == task_id)
            .order_by(AgentMessage.created_at.desc())
            .limit(limit)
        )
        return list(reversed(list(self.session.scalars(stmt))))
