from __future__ import annotations

from pathlib import Path

from astra_nexus.core.events import TaskEvent
from astra_nexus.core.orchestrator import TaskExecutionContext, TaskRunResult
from astra_nexus.db.models import AgentMessage, Task
from astra_nexus.telegram.presenters.agent_messages import agent_title


def render_task_accepted(context: TaskExecutionContext, agent_ids: list[str]) -> str:
    agents = " -> ".join(agent_title(agent_id) for agent_id in agent_ids)
    return (
        "Astra Nexus\n"
        f"Задача принята: {context.task_id}\n"
        f"Run: {context.run_id}\n"
        "Статус: running\n"
        f"Агенты: {agents}\n"
        f"Workspace: {context.workspace_path}"
    )


def render_task_result(result: TaskRunResult) -> str:
    return render_final_result(result)


def render_final_result(result: TaskRunResult) -> str:
    return (
        "Astra Nexus\n"
        f"Задача завершена: {result.task_id}\n"
        f"Run: {result.run_id}\n\n"
        f"{result.final_text}\n\n"
        f"Артефакт: {result.artifact_path}"
    )


def render_task_status(
    task: Task,
    recent_messages: list[AgentMessage] | None = None,
    workspace_path: Path | None = None,
    final_text: str | None = None,
) -> str:
    lines = [
        "Astra Nexus",
        f"Задача: {task.id}",
        f"Статус: {task.state}",
        f"Название: {task.title}",
    ]
    if workspace_path is not None:
        lines.append(f"Workspace: {workspace_path}")
    if recent_messages:
        lines.append("")
        lines.append("Последние сообщения:")
        for message in recent_messages:
            preview = message.content.strip().replace("\n", " ")
            if len(preview) > 180:
                preview = f"{preview[:177]}..."
            lines.append(f"- {agent_title(message.agent_id)}: {preview}")
    if final_text:
        lines.append("")
        lines.append("Итог:")
        lines.append(final_text)
    return "\n".join(lines)


def render_task_cancelled(task: Task) -> str:
    return (
        "Astra Nexus\n"
        f"Задача отменена: {task.id}\n"
        "Статус: cancelled\n"
        "TODO: для уже running-задач остановка выполняется между шагами агентов."
    )


def render_task_event(event: TaskEvent) -> str | None:
    if event.type == "task.done":
        return (
            "Astra Nexus\n"
            f"Задача завершена: {event.task_id}\n\n"
            f"{event.payload.get('final_text', '')}\n\n"
            f"Артефакт: {event.payload.get('artifact_path', '')}"
        )
    if event.type == "task.failed":
        status = str(event.payload.get("status", "failed"))
        message = str(event.payload.get("message", "задача завершилась с ошибкой"))
        action = str(event.payload.get("action", "проверь server logs"))
        if status in {
            "browser_connect_failed",
            "login_required",
            "timeout",
            "selector_not_found",
            "unavailable",
        }:
            return (
                "Astra Nexus\n"
                "Провайдер мозга недоступен\n"
                f"Задача: {event.task_id}\n"
                f"Причина: {message}\n"
                f"Что сделать: {action}"
            )
        return f"Astra Nexus\nЗадача завершилась с ошибкой: {event.task_id}\nПричина: {message}"
    if event.type == "task.cancelled":
        return f"Astra Nexus\nЗадача отменена: {event.task_id}"
    return None
