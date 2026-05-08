from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from astra_nexus.core.orchestrator import TaskOrchestrator
from astra_nexus.db.models import Task
from astra_nexus.services.task_service import TaskService

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class TaskCreate(BaseModel):
    prompt: str = Field(min_length=1)
    title: str | None = None
    user_id: str = "api:local"


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: str
    prompt: str
    state: str
    created_at: datetime
    updated_at: datetime


class TaskRunRead(BaseModel):
    task_id: str
    run_id: str
    state: str
    final_text: str
    artifact_path: str


@router.get("", response_model=list[TaskRead])
def list_tasks(request: Request) -> list[Task]:
    task_service: TaskService = request.app.state.task_service
    return task_service.list_tasks()


@router.post("", response_model=TaskRunRead)
def create_task(payload: TaskCreate, request: Request) -> TaskRunRead:
    orchestrator: TaskOrchestrator = request.app.state.orchestrator
    title = payload.title or payload.prompt[:80]
    result = orchestrator.run_task(user_id=payload.user_id, title=title, prompt=payload.prompt)
    return TaskRunRead(
        task_id=result.task_id,
        run_id=result.run_id,
        state="done",
        final_text=result.final_text,
        artifact_path=str(result.artifact_path),
    )


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: str, request: Request) -> Task:
    task_service: TaskService = request.app.state.task_service
    task = task_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return task
