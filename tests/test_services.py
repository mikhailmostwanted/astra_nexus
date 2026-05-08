from astra_nexus.core.task_state import TaskState
from astra_nexus.db.session import create_session_factory, init_db
from astra_nexus.services.message_service import MessageService
from astra_nexus.services.task_service import TaskService


def test_task_service_creates_task_and_latest_run() -> None:
    session_factory = create_session_factory("sqlite:///:memory:")
    init_db(session_factory)
    task_service = TaskService(session_factory)

    task = task_service.create_task(
        user_id="telegram:42",
        title="Новая задача",
        prompt="Сделать план",
    )
    run = task_service.create_run(task.id, TaskState.PLANNED)

    latest_run = task_service.get_latest_run(task.id)

    assert task.id.startswith("task_")
    assert latest_run is not None
    assert latest_run.id == run.id


def test_message_service_lists_task_history_in_order() -> None:
    session_factory = create_session_factory("sqlite:///:memory:")
    init_db(session_factory)
    task_service = TaskService(session_factory)
    message_service = MessageService(session_factory)

    task = task_service.create_task("telegram:42", "История", "Проверить историю")
    run = task_service.create_run(task.id)
    message_service.create_message(
        task_id=task.id,
        run_id=run.id,
        agent_id="coordinator",
        role="coordinator",
        content="Первое сообщение",
    )
    message_service.create_message(
        task_id=task.id,
        run_id=run.id,
        agent_id="writer",
        role="writer",
        content="Второе сообщение",
    )

    history = message_service.list_for_task(task.id, limit=10)

    assert [message.content for message in history] == ["Первое сообщение", "Второе сообщение"]
