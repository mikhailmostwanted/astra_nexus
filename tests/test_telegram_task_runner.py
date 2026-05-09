from pathlib import Path

import pytest

from astra_nexus.brain.base import BrainProvider, BrainProviderError, BrainResponse
from astra_nexus.core.orchestrator import TaskOrchestrator
from astra_nexus.db.session import create_session_factory, init_db
from astra_nexus.services.agent_service import AgentService
from astra_nexus.services.message_service import MessageService
from astra_nexus.services.task_service import TaskService
from astra_nexus.telegram.notifier import TelegramEventNotifier
from astra_nexus.telegram.task_runner import TelegramTaskRunner


class FailingBrainError(BrainProviderError):
    status = "prompt_box_not_found"
    action = "запусти astra-nexus-nodriver-ask для проверки"

    def __init__(self) -> None:
        super().__init__("Поле ввода ChatGPT не найдено.")
        self.stage = "chatgpt.prompt_box.search.started"
        self.provider = "nodriver"


class FailingBrainProvider(BrainProvider):
    name = "nodriver"

    def ask(
        self,
        agent_id: str,
        prompt: str,
        context: dict | None = None,
    ) -> BrainResponse:
        raise FailingBrainError()


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_message(self, *, chat_id: int, text: str) -> None:
        self.messages.append(text)


class FakeNotifier(TelegramEventNotifier):
    def __init__(self) -> None:
        self.bot = FakeBot()
        self.chat_id = 42
        self.events = []

    def __call__(self, event) -> None:
        self.events.append(event)
        text = self._render(event)
        if text is not None:
            self.bot.messages.append(text)


@pytest.mark.asyncio
async def test_task_runner_does_not_send_generic_error_after_detailed_failed_event(
    tmp_path: Path,
) -> None:
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'runner.sqlite3'}")
    init_db(session_factory)
    orchestrator = TaskOrchestrator(
        task_service=TaskService(session_factory),
        agent_service=AgentService(session_factory),
        message_service=MessageService(session_factory),
        brain_provider=FailingBrainProvider(),
        workspace_base_path=tmp_path,
    )
    runner = TelegramTaskRunner(orchestrator)
    context = orchestrator.create_task(
        user_id="telegram:42",
        title="Ошибка",
        prompt="Спровоцировать ошибку",
    )
    notifier = FakeNotifier()

    await runner._execute(context, notifier)

    assert any("error_code: prompt_box_not_found" in message for message in notifier.bot.messages)
    assert not any(
        message.strip() == f"Astra Nexus\nЗадача завершилась с ошибкой: {context.task_id}"
        for message in notifier.bot.messages
    )
