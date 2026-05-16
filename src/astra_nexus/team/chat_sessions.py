from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AgentChatSession:
    agent_id: str
    chat_url: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_used_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: str = "idle"  # idle, busy, error, closed
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentChatSessionRegistry:
    def __init__(self) -> None:
        self.sessions: dict[str, AgentChatSession] = {}

    def register(self, agent_id: str, chat_url: str | None = None) -> AgentChatSession:
        session = AgentChatSession(agent_id=agent_id, chat_url=chat_url)
        self.sessions[agent_id] = session
        return session

    def get(self, agent_id: str) -> AgentChatSession | None:
        return self.sessions.get(agent_id)

    def update_activity(
        self, agent_id: str, chat_url: str | None = None, status: str | None = None
    ):
        session = self.get(agent_id)
        if session:
            session.last_used_at = datetime.now(UTC)
            if chat_url:
                session.chat_url = chat_url
            if status:
                session.status = status

    def list_sessions(self) -> list[AgentChatSession]:
        return list(self.sessions.values())

    def clear(self):
        self.sessions.clear()

    def get_diagnostics(self) -> dict[str, Any]:
        return {
            "total_sessions": len(self.sessions),
            "sessions": [
                {
                    "agent_id": s.agent_id,
                    "chat_url": s.chat_url,
                    "status": s.status,
                    "last_used_at": s.last_used_at.isoformat(),
                }
                for s in self.sessions.values()
            ],
        }
