from pathlib import Path

from astra_nexus.core.events import TaskEvent
from astra_nexus.core.orchestrator import TaskExecutionContext, TaskRunResult
from astra_nexus.db.models import Agent, AgentMessage, Task
from astra_nexus.telegram.presenters.agent_messages import render_agent_message, render_agents
from astra_nexus.telegram.presenters.task_cards import (
    render_final_result,
    render_task_accepted,
    render_task_status,
)


def test_render_task_accepted_contains_task_id_and_agents() -> None:
    context = TaskExecutionContext(
        task_id="task_123",
        run_id="run_456",
        workspace_path=Path("data/workspaces/task_123"),
    )

    text = render_task_accepted(context, ["coordinator", "researcher", "writer"])

    assert "Astra Nexus" in text
    assert "task_123" in text
    assert "run_456" in text
    assert "Coordinator -> Researcher -> Writer" in text


def test_render_agent_message_uses_event_payload() -> None:
    event = TaskEvent(
        type="agent.message",
        task_id="task_123",
        run_id="run_456",
        payload={
            "agent_id": "coordinator",
            "role": "coordinator",
            "content": "Планирую маршрут работы.",
        },
    )

    text = render_agent_message(event)

    assert "Coordinator" in text
    assert "Планирую маршрут работы." in text


def test_render_status_includes_recent_messages_and_final_result() -> None:
    task = Task(
        id="task_123",
        user_id="telegram:42",
        title="План MVP",
        prompt="Сделать план",
        state="done",
    )
    messages = [
        AgentMessage(
            id="msg_1",
            task_id="task_123",
            run_id="run_456",
            agent_id="finalizer",
            role="finalizer",
            content="Итог: задача закрыта.",
        )
    ]
    result = TaskRunResult(
        task_id="task_123",
        run_id="run_456",
        final_text="Итог: задача закрыта.",
        artifact_path=Path("data/workspaces/task_123/artifacts/final.md"),
    )

    status_text = render_task_status(
        task,
        recent_messages=messages,
        workspace_path=Path("data/workspaces/task_123"),
        final_text=result.final_text,
    )
    final_text = render_final_result(result)

    assert "Статус: done" in status_text
    assert "Finalizer" in status_text
    assert "data/workspaces/task_123" in status_text
    assert "Артефакт" in final_text


def test_render_agents_shows_role_status_and_description() -> None:
    agents = [
        Agent(
            id="coordinator",
            role="coordinator",
            name="Координатор",
            description="Разбивает задачу на этапы.",
            is_active=True,
        )
    ]

    text = render_agents(agents)

    assert "coordinator" in text
    assert "active" in text
    assert "Разбивает задачу" in text
