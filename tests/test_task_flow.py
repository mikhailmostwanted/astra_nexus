from pathlib import Path

import pytest

from astra_nexus.brain.base import BrainProvider, BrainProviderError, BrainResponse
from astra_nexus.brain.dummy_provider import DummyBrainProvider
from astra_nexus.core.orchestrator import TaskOrchestrator
from astra_nexus.core.task_state import TaskState
from astra_nexus.db.models import AgentMessage, Task, TaskRun
from astra_nexus.db.session import create_session_factory, init_db
from astra_nexus.services.agent_service import AgentService
from astra_nexus.services.message_service import MessageService
from astra_nexus.services.task_service import TaskService


class FailingBrainError(BrainProviderError):
    status = "prompt_box_not_found"
    action = "запусти astra-nexus-nodriver-ask для проверки"

    def __init__(self) -> None:
        super().__init__("Поле ввода ChatGPT не найдено.")
        self.stage = "chatgpt.prompt_box.search.started"
        self.provider = "nodriver"
        self.debug_report_path = "data/workspaces/task_123/debug/nodriver_error.json"


class FailingBrainProvider(BrainProvider):
    name = "nodriver"

    def ask(
        self,
        agent_id: str,
        prompt: str,
        context: dict | None = None,
    ) -> BrainResponse:
        raise FailingBrainError()


def test_orchestrator_runs_task_and_persists_agent_messages(tmp_path: Path) -> None:
    session_factory = create_session_factory("sqlite:///:memory:")
    init_db(session_factory)

    orchestrator = TaskOrchestrator(
        task_service=TaskService(session_factory),
        agent_service=AgentService(session_factory),
        message_service=MessageService(session_factory),
        brain_provider=DummyBrainProvider(),
        workspace_base_path=tmp_path,
    )

    result = orchestrator.run_task(
        user_id="telegram:42", title="Сделать план", prompt="Нужен план MVP"
    )

    with session_factory() as session:
        task = session.get(Task, result.task_id)
        run = session.get(TaskRun, result.run_id)
        messages = session.query(AgentMessage).filter_by(run_id=result.run_id).all()

    assert result.final_text.startswith("Итог:")
    assert task is not None
    assert task.state == TaskState.DONE.value
    assert run is not None
    assert len(messages) == 5
    assert [message.agent_id for message in messages] == [
        "coordinator",
        "researcher",
        "writer",
        "critic",
        "finalizer",
    ]


def test_orchestrator_emits_agent_events_for_telegram_log(tmp_path: Path) -> None:
    session_factory = create_session_factory("sqlite:///:memory:")
    init_db(session_factory)
    events = []

    orchestrator = TaskOrchestrator(
        task_service=TaskService(session_factory),
        agent_service=AgentService(session_factory),
        message_service=MessageService(session_factory),
        brain_provider=DummyBrainProvider(),
        workspace_base_path=tmp_path,
    )

    result = orchestrator.run_task(
        user_id="telegram:42",
        title="Сделать план",
        prompt="Нужен план MVP",
        event_sink=events.append,
    )

    agent_events = [event for event in events if event.type == "agent.message"]

    assert result.task_id
    assert len(agent_events) == 5
    assert agent_events[0].payload["agent_id"] == "coordinator"
    assert agent_events[0].payload["content"].startswith("План:")
    assert events[-1].type == "task.done"


def test_orchestrator_persists_failed_agent_error_message(tmp_path: Path) -> None:
    session_factory = create_session_factory("sqlite:///:memory:")
    init_db(session_factory)
    events = []

    orchestrator = TaskOrchestrator(
        task_service=TaskService(session_factory),
        agent_service=AgentService(session_factory),
        message_service=MessageService(session_factory),
        brain_provider=FailingBrainProvider(),
        workspace_base_path=tmp_path,
    )
    context = orchestrator.create_task(
        user_id="telegram:42",
        title="Ошибка",
        prompt="Спровоцировать ошибку",
    )

    with pytest.raises(FailingBrainError):
        orchestrator.execute_task(context, event_sink=events.append)

    with session_factory() as session:
        task = session.get(Task, context.task_id)
        run = session.get(TaskRun, context.run_id)
        messages = session.query(AgentMessage).filter_by(run_id=context.run_id).all()

    assert task is not None
    assert task.state == TaskState.FAILED.value
    assert run is not None
    assert run.state == TaskState.FAILED.value
    assert len(messages) == 1
    assert messages[0].agent_id == "coordinator"
    assert messages[0].role == "error"
    assert messages[0].metadata_json["error_code"] == "prompt_box_not_found"
    assert messages[0].metadata_json["stage"] == "chatgpt.prompt_box.search.started"
    assert events[-1].type == "task.failed"
    assert events[-1].payload["agent_id"] == "coordinator"
    assert events[-1].payload["stage"] == "chatgpt.prompt_box.search.started"
