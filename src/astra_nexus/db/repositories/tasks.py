from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from astra_nexus.core.task_state import TaskState
from astra_nexus.db.models import Artifact, Task, TaskRun
from astra_nexus.utils.ids import new_id


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, user_id: str, title: str, prompt: str) -> Task:
        task = Task(
            id=new_id("task"),
            user_id=user_id,
            title=title,
            prompt=prompt,
            state=TaskState.NEW.value,
        )
        self.session.add(task)
        return task

    def get(self, task_id: str) -> Task | None:
        return self.session.get(Task, task_id)

    def list_recent(self, limit: int = 20) -> list[Task]:
        stmt = select(Task).order_by(Task.created_at.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def update_state(self, task: Task, state: TaskState) -> Task:
        task.state = state.value
        return task

    def create_run(self, task_id: str, state: TaskState = TaskState.PLANNED) -> TaskRun:
        run = TaskRun(id=new_id("run"), task_id=task_id, state=state.value)
        self.session.add(run)
        return run

    def complete_run(self, run: TaskRun, state: TaskState) -> TaskRun:
        run.state = state.value
        run.completed_at = datetime.now(UTC)
        return run

    def create_artifact(self, task_id: str, run_id: str, path: str, kind: str) -> Artifact:
        artifact = Artifact(
            id=new_id("artifact"), task_id=task_id, run_id=run_id, path=path, kind=kind
        )
        self.session.add(artifact)
        return artifact
