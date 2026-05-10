from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from astra_nexus.team.models import AgentRole, utc_now

BOOTSTRAP_STATUSES = {"missing", "created", "bootstrapped", "failed"}


@dataclass(frozen=True)
class AgentChatSession:
    agent_role: AgentRole
    display_name: str
    chat_url: str
    conversation_id: str | None = None
    bootstrap_status: str = "missing"
    preferred_model_name: str | None = None
    preferred_reasoning_mode: str | None = None
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.bootstrap_status not in BOOTSTRAP_STATUSES:
            raise ValueError(f"Unknown agent chat bootstrap status: {self.bootstrap_status}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_role": self.agent_role.value,
            "display_name": self.display_name,
            "chat_url": self.chat_url,
            "conversation_id": self.conversation_id,
            "bootstrap_status": self.bootstrap_status,
            "preferred_model_name": self.preferred_model_name,
            "preferred_reasoning_mode": self.preferred_reasoning_mode,
            "last_used_at": _serialize_datetime(self.last_used_at),
            "created_at": _serialize_datetime(self.created_at),
            "updated_at": _serialize_datetime(self.updated_at),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AgentChatSession:
        return cls(
            agent_role=AgentRole(payload["agent_role"]),
            display_name=str(payload.get("display_name") or payload["agent_role"]),
            chat_url=str(payload.get("chat_url") or ""),
            conversation_id=_str_or_none(payload.get("conversation_id")),
            bootstrap_status=str(payload.get("bootstrap_status") or "missing"),
            preferred_model_name=_str_or_none(payload.get("preferred_model_name")),
            preferred_reasoning_mode=_str_or_none(payload.get("preferred_reasoning_mode")),
            last_used_at=_parse_optional_datetime(payload.get("last_used_at")),
            created_at=_parse_datetime(payload.get("created_at")),
            updated_at=_parse_datetime(payload.get("updated_at")),
        )


class AgentChatSessionRegistry:
    def __init__(
        self,
        *,
        path: Path | str | None = None,
        root_dir: Path | str = "data",
    ) -> None:
        self.path = (
            Path(path)
            if path is not None
            else Path(root_dir) / "team_agent_chats" / "agent_chats.json"
        )
        self._sessions: dict[AgentRole, AgentChatSession] = {}

    def load(self) -> None:
        if not self.path.exists():
            self._sessions = {}
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        sessions = payload.get("sessions", []) if isinstance(payload, dict) else []
        self._sessions = {
            session.agent_role: session
            for session in (
                AgentChatSession.from_dict(item) for item in sessions if isinstance(item, dict)
            )
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sessions": [session.as_dict() for session in self.list()],
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def get_by_role(self, role: AgentRole | str) -> AgentChatSession | None:
        return self._sessions.get(AgentRole(role))

    def upsert(self, session: AgentChatSession) -> None:
        self._sessions[session.agent_role] = session

    def list(self) -> list[AgentChatSession]:
        order = {role: index for index, role in enumerate(AgentRole)}
        return sorted(self._sessions.values(), key=lambda item: order.get(item.agent_role, 999))


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return utc_now()


def _parse_optional_datetime(value: object) -> datetime | None:
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return None


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
