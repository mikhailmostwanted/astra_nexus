from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astra_nexus.agents.registry import AgentRegistry, create_default_registry
from astra_nexus.brain.base import BrainProvider
from astra_nexus.core.events import EventSink, TaskEvent
from astra_nexus.core.task_state import TaskState
from astra_nexus.core.workspace import WorkspaceManager
from astra_nexus.services.agent_service import AgentService
from astra_nexus.services.message_service import MessageService
from astra_nexus.services.task_service import TaskService


@dataclass(frozen=True)
class TaskRunResult:
    task_id: str
    run_id: str
    final_text: str
    artifact_path: Path


@dataclass(frozen=True)
class TaskExecutionContext:
    task_id: str
    run_id: str
    workspace_path: Path


class TaskOrchestrator:
    def __init__(
        self,
        *,
        task_service: TaskService,
        agent_service: AgentService,
        message_service: MessageService,
        brain_provider: BrainProvider,
        workspace_base_path: Path | str,
        registry: AgentRegistry | None = None,
    ) -> None:
        self.task_service = task_service
        self.agent_service = agent_service
        self.message_service = message_service
        self.brain_provider = brain_provider
        self.workspace_manager = WorkspaceManager(workspace_base_path)
        self.registry = registry or create_default_registry()

    def create_task(
        self,
        user_id: str,
        title: str,
        prompt: str,
        event_sink: EventSink | None = None,
    ) -> TaskExecutionContext:
        self.agent_service.sync_registry(self.registry)
        task = self.task_service.create_task(user_id=user_id, title=title, prompt=prompt)
        run = self.task_service.create_run(task_id=task.id, state=TaskState.PLANNED)
        workspace = self.workspace_manager.create_for_task(task.id)
        self._record_event(
            TaskEvent(
                type="task.created",
                task_id=task.id,
                run_id=run.id,
                payload={"title": title},
            ),
            event_sink,
        )
        return TaskExecutionContext(task_id=task.id, run_id=run.id, workspace_path=workspace.root)

    def run_task(
        self,
        user_id: str,
        title: str,
        prompt: str,
        event_sink: EventSink | None = None,
    ) -> TaskRunResult:
        context = self.create_task(
            user_id=user_id,
            title=title,
            prompt=prompt,
            event_sink=event_sink,
        )
        return self.execute_task(context, event_sink=event_sink)

    def execute_task(
        self,
        context: TaskExecutionContext,
        event_sink: EventSink | None = None,
    ) -> TaskRunResult:
        task = self.task_service.get_task(context.task_id)
        if task is None:
            raise ValueError(f"Задача не найдена: {context.task_id}")

        previous_messages: list[dict[str, Any]] = []
        final_text = ""
        artifact_path = context.workspace_path / "artifacts" / "final.md"

        try:
            self._set_state(context, TaskState.PLANNED, event_sink)
            for agent in self.registry.all():
                if self.task_service.is_cancelled(context.task_id):
                    self.task_service.complete_run(context.run_id, TaskState.CANCELLED)
                    self._record_event(
                        TaskEvent(
                            type="task.cancelled",
                            task_id=context.task_id,
                            run_id=context.run_id,
                        ),
                        event_sink,
                    )
                    return TaskRunResult(
                        task_id=context.task_id,
                        run_id=context.run_id,
                        final_text="Задача отменена.",
                        artifact_path=artifact_path,
                    )

                next_state = (
                    TaskState.FINALIZING if agent.agent_id == "finalizer" else TaskState.RUNNING
                )
                self._set_state(context, next_state, event_sink, agent_id=agent.agent_id)

                output = agent.run(
                    brain_provider=self.brain_provider,
                    task_prompt=task.prompt,
                    context={
                        "task_id": context.task_id,
                        "run_id": context.run_id,
                        "previous_messages": previous_messages,
                    },
                )
                message = self.message_service.create_message(
                    task_id=context.task_id,
                    run_id=context.run_id,
                    agent_id=output.agent_id,
                    role=output.role,
                    content=output.content,
                    metadata=output.metadata,
                )
                previous_messages.append(
                    {"agent_id": output.agent_id, "role": output.role, "content": output.content}
                )
                final_text = output.content
                self._record_event(
                    TaskEvent(
                        type="agent.message",
                        task_id=context.task_id,
                        run_id=context.run_id,
                        payload={
                            "agent_id": output.agent_id,
                            "role": output.role,
                            "content": output.content,
                            "message_id": message.id,
                        },
                    ),
                    event_sink,
                )

            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(final_text, encoding="utf-8")
            self.task_service.create_artifact(
                task_id=context.task_id,
                run_id=context.run_id,
                path=str(artifact_path),
                kind="final_markdown",
            )
            self.task_service.update_task_state(context.task_id, TaskState.DONE)
            self.task_service.complete_run(context.run_id, TaskState.DONE)
            self._record_event(
                TaskEvent(
                    type="task.done",
                    task_id=context.task_id,
                    run_id=context.run_id,
                    payload={"final_text": final_text, "artifact_path": str(artifact_path)},
                ),
                event_sink,
            )
            return TaskRunResult(
                task_id=context.task_id,
                run_id=context.run_id,
                final_text=final_text,
                artifact_path=artifact_path,
            )
        except Exception:
            self.task_service.update_task_state(context.task_id, TaskState.FAILED)
            self.task_service.complete_run(context.run_id, TaskState.FAILED)
            self._record_event(
                TaskEvent(type="task.failed", task_id=context.task_id, run_id=context.run_id),
                event_sink,
            )
            raise

    def _set_state(
        self,
        context: TaskExecutionContext,
        state: TaskState,
        event_sink: EventSink | None,
        agent_id: str | None = None,
    ) -> None:
        self.task_service.update_task_state(context.task_id, state)
        self._record_event(
            TaskEvent(
                type="task.stage_changed",
                task_id=context.task_id,
                run_id=context.run_id,
                payload={"state": state.value, "agent_id": agent_id},
            ),
            event_sink,
        )

    def _record_event(self, event: TaskEvent, event_sink: EventSink | None = None) -> None:
        self.workspace_manager.append_event(event.task_id, event.as_dict())
        if event_sink is not None:
            event_sink(event)
