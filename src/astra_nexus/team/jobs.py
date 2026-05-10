from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from astra_nexus.team.models import utc_now
from astra_nexus.team.runtime import TeamRuntimeResponse, TeamRuntimeStatus
from astra_nexus.utils.ids import new_id


class TeamJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TeamJobAlreadyActiveError(RuntimeError):
    def __init__(self, session_id: str, job_id: str) -> None:
        super().__init__(f"session {session_id} already has active job {job_id}")
        self.session_id = session_id
        self.job_id = job_id


@dataclass
class TeamJob:
    session_id: str
    user_task: str
    id: str = field(default_factory=lambda: new_id("team_job"))
    status: TeamJobStatus = TeamJobStatus.PENDING
    task: asyncio.Task[None] | None = None
    response: TeamRuntimeResponse | None = None
    run_id: str | None = None
    final_text: str | None = None
    workspace_path: Path | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def snapshot(self) -> TeamJobSnapshot:
        return TeamJobSnapshot(
            job_id=self.id,
            session_id=self.session_id,
            status=self.status,
            user_task=self.user_task,
            run_id=self.run_id,
            final_text=self.final_text,
            workspace_path=self.workspace_path,
            error_message=self.error_message,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
        )


@dataclass(frozen=True)
class TeamJobSnapshot:
    job_id: str
    session_id: str
    status: TeamJobStatus
    user_task: str
    run_id: str | None = None
    final_text: str | None = None
    workspace_path: Path | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TeamJobHandle:
    def __init__(self, job: TeamJob) -> None:
        self.job = job

    def done(self) -> bool:
        return self.job.task.done() if self.job.task is not None else True

    def snapshot(self) -> TeamJobSnapshot:
        return self.job.snapshot()

    async def wait(self) -> TeamJobSnapshot:
        if self.job.task is not None:
            await self.job.task
        return self.job.snapshot()

    def cancel(self, reason: str = "cancelled") -> TeamJobSnapshot:
        self.job.status = TeamJobStatus.CANCELLED
        self.job.error_message = reason
        self.job.finished_at = utc_now()
        if self.job.task is not None and not self.job.task.done():
            self.job.task.cancel()
        return self.job.snapshot()


JobRunner = Callable[[], Awaitable[TeamRuntimeResponse]]


class TeamJobManager:
    def __init__(self) -> None:
        self.active_jobs: dict[str, TeamJob] = {}
        self.last_jobs: dict[str, TeamJob] = {}
        self.last_completed_jobs: dict[str, TeamJob] = {}
        self.last_failed_jobs: dict[str, TeamJob] = {}
        self.last_cancelled_jobs: dict[str, TeamJob] = {}

    def start(
        self,
        *,
        session_id: str,
        user_task: str,
        runner: JobRunner,
    ) -> TeamJobHandle:
        active = self.active_jobs.get(session_id)
        if active is not None and active.status in {TeamJobStatus.PENDING, TeamJobStatus.RUNNING}:
            raise TeamJobAlreadyActiveError(session_id=session_id, job_id=active.id)

        job = TeamJob(session_id=session_id, user_task=user_task)
        self.active_jobs[session_id] = job
        job.task = asyncio.create_task(self._run_job(job, runner))
        return TeamJobHandle(job)

    def active(self, session_id: str) -> TeamJobSnapshot | None:
        job = self.active_jobs.get(session_id)
        return job.snapshot() if job is not None else None

    def snapshot(self, session_id: str) -> TeamJobSnapshot | None:
        job = self.active_jobs.get(session_id) or self.last_jobs.get(session_id)
        return job.snapshot() if job is not None else None

    def last_completed(self, session_id: str) -> TeamJobSnapshot | None:
        job = self.last_completed_jobs.get(session_id)
        return job.snapshot() if job is not None else None

    def last_failed(self, session_id: str) -> TeamJobSnapshot | None:
        job = self.last_failed_jobs.get(session_id)
        return job.snapshot() if job is not None else None

    def last_cancelled(self, session_id: str) -> TeamJobSnapshot | None:
        job = self.last_cancelled_jobs.get(session_id)
        return job.snapshot() if job is not None else None

    async def wait(self, session_id: str) -> TeamJobSnapshot:
        job = self.active_jobs.get(session_id) or self.last_jobs.get(session_id)
        if job is None:
            raise KeyError(f"session {session_id} has no jobs")
        if job.task is not None and not job.task.done():
            await job.task
        return job.snapshot()

    async def cancel_active(
        self,
        session_id: str,
        *,
        reason: str = "cancelled",
    ) -> TeamJobSnapshot | None:
        job = self.active_jobs.pop(session_id, None)
        if job is None:
            return None
        job.status = TeamJobStatus.CANCELLED
        job.error_message = reason
        job.finished_at = utc_now()
        self._remember(job)
        if job.task is not None and not job.task.done():
            job.task.cancel()
            await asyncio.sleep(0)
        return job.snapshot()

    async def _run_job(self, job: TeamJob, runner: JobRunner) -> None:
        job.status = TeamJobStatus.RUNNING
        job.started_at = utc_now()
        try:
            response = await runner()
        except asyncio.CancelledError:
            if job.status != TeamJobStatus.CANCELLED:
                job.status = TeamJobStatus.CANCELLED
                job.error_message = job.error_message or "cancelled"
                job.finished_at = utc_now()
                self._remember(job)
            return
        except Exception as exc:
            job.status = TeamJobStatus.FAILED
            job.error_message = str(exc)
            job.finished_at = utc_now()
            self._remember(job)
            return

        if job.status == TeamJobStatus.CANCELLED:
            self._remember(job)
            return

        job.response = response
        job.run_id = response.run_id
        job.final_text = response.final_text
        job.workspace_path = response.workspace_path
        job.status = self._status_from_response(response)
        if job.status == TeamJobStatus.FAILED:
            job.error_message = response.user_visible_reply
        job.finished_at = utc_now()
        self._remember(job)

    def _status_from_response(self, response: TeamRuntimeResponse) -> TeamJobStatus:
        if response.status == TeamRuntimeStatus.COMPLETED:
            return TeamJobStatus.COMPLETED
        if response.status == TeamRuntimeStatus.FAILED:
            return TeamJobStatus.FAILED
        if response.status == TeamRuntimeStatus.CANCELLED:
            return TeamJobStatus.CANCELLED
        return TeamJobStatus.RUNNING

    def _remember(self, job: TeamJob) -> None:
        self.active_jobs.pop(job.session_id, None)
        self.last_jobs[job.session_id] = job
        if job.status == TeamJobStatus.COMPLETED:
            self.last_completed_jobs[job.session_id] = job
        elif job.status == TeamJobStatus.FAILED:
            self.last_failed_jobs[job.session_id] = job
        elif job.status == TeamJobStatus.CANCELLED:
            self.last_cancelled_jobs[job.session_id] = job
