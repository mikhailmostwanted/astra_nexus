from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class TaskEvent:
    type: str
    task_id: str
    run_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "payload": self.payload,
            "created_at": self.created_at.isoformat(),
        }
