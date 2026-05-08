from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from astra_nexus.db.models import AgentMessage
from astra_nexus.db.repositories.messages import MessageRepository


class MessageService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create_message(
        self,
        *,
        task_id: str,
        run_id: str,
        agent_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentMessage:
        with self.session_factory() as session:
            message = MessageRepository(session).create(
                task_id=task_id,
                run_id=run_id,
                agent_id=agent_id,
                role=role,
                content=content,
                metadata=metadata,
            )
            session.commit()
            return message

    def list_for_run(self, run_id: str) -> list[AgentMessage]:
        with self.session_factory() as session:
            return MessageRepository(session).list_for_run(run_id=run_id)
