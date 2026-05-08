from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astra_nexus.agents.registry import AgentRegistry, create_default_registry
from astra_nexus.brain.base import BrainProvider
from astra_nexus.core.events import TaskEvent
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

    def run_task(self, user_id: str, title: str, prompt: str) -> TaskRunResult:
        self.agent_service.sync_registry(self.registry)
        task = self.task_service.create_task(user_id=user_id, title=title, prompt=prompt)
        run = self.task_service.create_run(task_id=task.id, state=TaskState.PLANNED)
        workspace = self.workspace_manager.create_for_task(task.id)
        self._append_event(TaskEvent(type="task.created", task_id=task.id, run_id=run.id))

        previous_messages: list[dict[str, Any]] = []
        final_text = ""

        try:
            self.task_service.update_task_state(task.id, TaskState.PLANNED)
            for agent in self.registry.all():
                next_state = (
                    TaskState.FINALIZING if agent.agent_id == "finalizer" else TaskState.RUNNING
                )
                self.task_service.update_task_state(task.id, next_state)

                output = agent.run(
                    brain_provider=self.brain_provider,
                    task_prompt=prompt,
                    context={
                        "task_id": task.id,
                        "run_id": run.id,
                        "previous_messages": previous_messages,
                    },
                )
                self.message_service.create_message(
                    task_id=task.id,
                    run_id=run.id,
                    agent_id=output.agent_id,
                    role=output.role,
                    content=output.content,
                    metadata=output.metadata,
                )
                previous_messages.append(
                    {"agent_id": output.agent_id, "role": output.role, "content": output.content}
                )
                final_text = output.content
                self._append_event(
                    TaskEvent(
                        type="agent.message",
                        task_id=task.id,
                        run_id=run.id,
                        payload={"agent_id": output.agent_id, "role": output.role},
                    )
                )

            artifact_path = workspace.artifacts_dir / "final.md"
            artifact_path.write_text(final_text, encoding="utf-8")
            self.task_service.create_artifact(
                task_id=task.id,
                run_id=run.id,
                path=str(artifact_path),
                kind="final_markdown",
            )
            self.task_service.update_task_state(task.id, TaskState.DONE)
            self.task_service.complete_run(run.id, TaskState.DONE)
            self._append_event(TaskEvent(type="task.done", task_id=task.id, run_id=run.id))
            return TaskRunResult(
                task_id=task.id,
                run_id=run.id,
                final_text=final_text,
                artifact_path=artifact_path,
            )
        except Exception:
            self.task_service.update_task_state(task.id, TaskState.FAILED)
            self.task_service.complete_run(run.id, TaskState.FAILED)
            self._append_event(TaskEvent(type="task.failed", task_id=task.id, run_id=run.id))
            raise

    def _append_event(self, event: TaskEvent) -> None:
        self.workspace_manager.append_event(event.task_id, event.as_dict())
