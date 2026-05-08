from __future__ import annotations

from enum import StrEnum


class TaskState(StrEnum):
    NEW = "new"
    PLANNED = "planned"
    RUNNING = "running"
    WAITING_REVIEW = "waiting_review"
    FINALIZING = "finalizing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
