from pathlib import Path

from astra_nexus.core.events import TaskEvent
from astra_nexus.core.orchestrator import TaskExecutionContext, TaskRunResult
from astra_nexus.db.models import Agent, AgentMessage, Task
from astra_nexus.telegram.presenters.agent_messages import render_agent_message, render_agents
from astra_nexus.telegram.presenters.task_cards import (
    render_final_result,
    render_task_accepted,
    render_task_event,
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


def test_render_brain_provider_error_event_without_traceback() -> None:
    event = TaskEvent(
        type="task.failed",
        task_id="task_123",
        run_id="run_456",
        payload={
            "status": "login_required",
            "message": "требуется ручной вход в ChatGPT",
            "action": "запусти astra-nexus-nodriver-login и авторизуйся",
        },
    )

    text = render_task_event(event)

    assert text is not None
    assert "Задача завершилась с ошибкой" in text
    assert "error_code: login_required" in text
    assert "требуется ручной вход" in text
    assert "astra-nexus-nodriver-login" in text
    assert "Traceback" not in text


def test_render_profile_locked_event_without_traceback() -> None:
    event = TaskEvent(
        type="task.failed",
        task_id="task_123",
        run_id="run_456",
        payload={
            "status": "profile_locked",
            "message": "Chrome profile занят другим процессом.",
            "action": "заверши astra-nexus-nodriver-login или выполни astra-nexus-nodriver-clean",
        },
    )

    text = render_task_event(event)

    assert text is not None
    assert "Задача завершилась с ошибкой" in text
    assert "error_code: profile_locked" in text
    assert "Chrome profile занят" in text
    assert "astra-nexus-nodriver-clean" in text
    assert "Traceback" not in text


def test_render_failed_task_event_includes_stage_agent_provider_and_error_code() -> None:
    event = TaskEvent(
        type="task.failed",
        task_id="task_123",
        run_id="run_456",
        payload={
            "status": "prompt_box_not_found",
            "stage": "chatgpt.prompt_box.search.started",
            "agent_id": "coordinator",
            "provider": "nodriver",
            "message": "Поле ввода ChatGPT не найдено.",
            "debug_report_path": "data/workspaces/task_123/debug/nodriver_error.json",
        },
    )

    text = render_task_event(event)

    assert text is not None
    assert "Задача завершилась с ошибкой" in text
    assert "task_id: task_123" in text
    assert "stage: chatgpt.prompt_box.search.started" in text
    assert "agent: coordinator" in text
    assert "provider: nodriver" in text
    assert "error_code: prompt_box_not_found" in text
    assert "Поле ввода ChatGPT не найдено." in text
    assert "debug: data/workspaces/task_123/debug/nodriver_error.json" in text


def test_render_status_includes_failed_error_metadata() -> None:
    task = Task(
        id="task_123",
        user_id="telegram:42",
        title="Проверка",
        prompt="Проверить",
        state="failed",
    )
    error_message = AgentMessage(
        id="msg_error",
        task_id="task_123",
        run_id="run_456",
        agent_id="coordinator",
        role="error",
        content="Поле ввода ChatGPT не найдено.",
        metadata_json={
            "stage": "chatgpt.prompt_box.search.started",
            "error_code": "prompt_box_not_found",
            "debug_report_path": "data/workspaces/task_123/debug/nodriver_error.json",
        },
    )

    text = render_task_status(
        task,
        recent_messages=[error_message],
        workspace_path=Path("data/workspaces/task_123"),
    )

    assert "failed stage: chatgpt.prompt_box.search.started" in text
    assert "failed agent: coordinator" in text
    assert "error_code: prompt_box_not_found" in text
    assert "error_message: Поле ввода ChatGPT не найдено." in text
    assert "debug report: data/workspaces/task_123/debug/nodriver_error.json" in text
