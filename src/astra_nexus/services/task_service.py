from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from astra_nexus.core.task_state import TaskState
from astra_nexus.db.models import Artifact, Task, TaskRun
from astra_nexus.db.repositories.tasks import TaskRepository


class TaskService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create_task(self, user_id: str, title: str, prompt: str) -> Task:
        with self.session_factory() as session:
            task = TaskRepository(session).create(user_id=user_id, title=title, prompt=prompt)
            session.commit()
            return task

    def get_task(self, task_id: str) -> Task | None:
        with self.session_factory() as session:
            return TaskRepository(session).get(task_id)

    def list_tasks(self, limit: int = 20) -> list[Task]:
        with self.session_factory() as session:
            return TaskRepository(session).list_recent(limit=limit)

    def update_task_state(self, task_id: str, state: TaskState) -> Task:
        with self.session_factory() as session:
            repository = TaskRepository(session)
            task = repository.get(task_id)
            if task is None:
                raise ValueError(f"Задача не найдена: {task_id}")
            repository.update_state(task, state)
            session.commit()
            return task

    def create_run(self, task_id: str, state: TaskState = TaskState.PLANNED) -> TaskRun:
        with self.session_factory() as session:
            run = TaskRepository(session).create_run(task_id=task_id, state=state)
            session.commit()
            return run

    def complete_run(self, run_id: str, state: TaskState) -> TaskRun:
        with self.session_factory() as session:
            run = session.get(TaskRun, run_id)
            if run is None:
                raise ValueError(f"Запуск задачи не найден: {run_id}")
            TaskRepository(session).complete_run(run, state)
            session.commit()
            return run

    def create_artifact(self, task_id: str, run_id: str, path: str, kind: str) -> Artifact:
        with self.session_factory() as session:
            artifact = TaskRepository(session).create_artifact(
                task_id=task_id,
                run_id=run_id,
                path=path,
                kind=kind,
            )
            session.commit()
            return artifact
