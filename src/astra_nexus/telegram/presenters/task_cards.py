from __future__ import annotations

from astra_nexus.core.orchestrator import TaskRunResult
from astra_nexus.db.models import Task


def render_task_result(result: TaskRunResult) -> str:
    return (
        f"Задача завершена: {result.task_id}\n"
        f"Run: {result.run_id}\n\n"
        f"{result.final_text}\n\n"
        f"Артефакт: {result.artifact_path}"
    )


def render_task_status(task: Task) -> str:
    return (
        f"Задача: {task.id}\n"
        f"Статус: {task.state}\n"
        f"Название: {task.title}\n"
        f"Создана: {task.created_at.isoformat()}"
    )
