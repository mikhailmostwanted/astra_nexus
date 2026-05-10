from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from astra_nexus.team.fake_provider import FakeTeamProvider
from astra_nexus.team.intake import (
    TeamInput,
    TeamInputIntent,
    TeamIntakeDecision,
    TeamIntakeRouter,
)
from astra_nexus.team.messages import TeamMessageSink
from astra_nexus.team.models import RunStatus, TeamRun, TeamRunOutcome, utc_now
from astra_nexus.team.orchestrator import AsyncTeamOrchestrator
from astra_nexus.team.provider import TeamProvider, TeamProviderError
from astra_nexus.team.workspace import TeamRunWorkspace


class TeamRuntimeStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TeamActiveRun:
    run_id: str
    user_task: str = ""
    status: TeamRuntimeStatus = TeamRuntimeStatus.RUNNING
    workspace_path: Path | None = None
    current_worker: str = "команда"
    stop_requested: bool = False
    stopped_at: datetime | None = None
    stop_reason: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def request_stop(self, reason: str) -> None:
        self.stop_requested = True
        self.status = TeamRuntimeStatus.CANCELLED
        self.stopped_at = utc_now()
        self.stop_reason = reason
        self.updated_at = self.stopped_at


@dataclass
class TeamRuntimeState:
    active_runs: dict[str, TeamActiveRun] = field(default_factory=dict)
    stopped_runs: dict[str, TeamActiveRun] = field(default_factory=dict)
    last_run_id: str | None = None
    last_completed_run_id: str | None = None
    last_failed_run_id: str | None = None
    last_workspace_path: Path | None = None
    last_result_preview: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def touch(self) -> None:
        self.updated_at = utc_now()


@dataclass(frozen=True)
class TeamRuntimeResponse:
    user_visible_reply: str
    decision: TeamIntakeDecision
    status: TeamRuntimeStatus = TeamRuntimeStatus.IDLE
    run_id: str | None = None
    final_text: str | None = None
    workspace_path: Path | None = None
    outcome: TeamRunOutcome | None = None
    state: TeamRuntimeState | None = None


OrchestratorFactory = Callable[[TeamProvider], AsyncTeamOrchestrator]


class TeamConversationController:
    def __init__(
        self,
        *,
        router: TeamIntakeRouter | None = None,
        provider: TeamProvider | None = None,
        workspace: TeamRunWorkspace | None = None,
        message_sink: TeamMessageSink | None = None,
        state: TeamRuntimeState | None = None,
        orchestrator_factory: OrchestratorFactory | None = None,
    ) -> None:
        self.router = router or TeamIntakeRouter()
        self.provider = provider or FakeTeamProvider()
        self.workspace = workspace
        self.message_sink = message_sink
        self.state = state or TeamRuntimeState()
        self.orchestrator_factory = orchestrator_factory
        self.runs: list[TeamRun] = []

    async def handle(
        self,
        incoming: TeamInput | str,
        **metadata: Any,
    ) -> TeamRuntimeResponse:
        team_input = self._team_input(incoming, **metadata)
        decision = self.router.route(team_input)

        if decision.intent in {
            TeamInputIntent.CASUAL_CHAT,
            TeamInputIntent.EMPTY_INPUT,
            TeamInputIntent.UNKNOWN,
        }:
            return self._response(decision=decision)

        if decision.intent == TeamInputIntent.STATUS_REQUEST:
            return self._status_response(decision)

        if decision.intent == TeamInputIntent.RUNS_REQUEST:
            return self._response(
                decision=decision,
                user_visible_reply=(
                    "История запусков доступна через Telegram /runs или CLI preview."
                ),
            )

        if decision.intent == TeamInputIntent.STOP_ALL:
            return self._stop_all(decision)

        if decision.intent == TeamInputIntent.RESUME_RUN:
            return await self._resume_run(decision)

        if decision.intent in {
            TeamInputIntent.NEW_TASK,
            TeamInputIntent.FILE_TASK,
            TeamInputIntent.TASK_FOLLOWUP,
            TeamInputIntent.REVISE_PREVIOUS_RESULT,
        }:
            if decision.intent == TeamInputIntent.FILE_TASK and not decision.should_start_run:
                return self._response(decision=decision)
            return await self._start_run(team_input=team_input, decision=decision)

        return self._response(decision=decision)

    async def _start_run(
        self,
        *,
        team_input: TeamInput,
        decision: TeamIntakeDecision,
    ) -> TeamRuntimeResponse:
        user_task = self._runtime_task_text(team_input=team_input, decision=decision)
        orchestrator = self._orchestrator()
        active = TeamActiveRun(run_id="pending", user_task=user_task)
        self._register_started(active)
        try:
            outcome = await orchestrator.run(user_task, attachments=team_input.attachments)
        except TeamProviderError as exc:
            failed_run = orchestrator.runs[-1] if orchestrator.runs else None
            if failed_run is None:
                self.state.active_runs.pop(active.run_id, None)
                return self._response(
                    decision=decision,
                    status=TeamRuntimeStatus.FAILED,
                    user_visible_reply=f"Команда завершилась с ошибкой: {exc}",
                )
            self.state.active_runs.pop(active.run_id, None)
            active.run_id = failed_run.id
            self._register_started(active)
            self._apply_run_metadata(failed_run, team_input.metadata)
            workspace_path = self._save(failed_run)
            self._register_failed(failed_run, workspace_path=workspace_path)
            self.runs.append(failed_run)
            return self._response(
                decision=decision,
                status=TeamRuntimeStatus.FAILED,
                run_id=failed_run.id,
                workspace_path=workspace_path,
                user_visible_reply=(
                    f"Команда завершилась с ошибкой. Run сохранён: {failed_run.id}"
                ),
            )

        self.state.active_runs.pop(active.run_id, None)
        active.run_id = outcome.run.id
        self._register_started(active)
        self._apply_run_metadata(outcome.run, team_input.metadata)
        workspace_path = self._save(outcome.run)
        self._register_completed(outcome.run, workspace_path=workspace_path)
        self.runs.append(outcome.run)
        return self._response(
            decision=decision,
            status=TeamRuntimeStatus.COMPLETED,
            run_id=outcome.run.id,
            final_text=outcome.final_text,
            workspace_path=workspace_path,
            outcome=outcome,
            user_visible_reply=outcome.final_text,
        )

    async def _resume_run(self, decision: TeamIntakeDecision) -> TeamRuntimeResponse:
        if self.workspace is None or not decision.target_run_id:
            return self._response(
                decision=decision,
                user_visible_reply="Resume пока доступен только для сохранённого workspace.",
            )

        run = self.workspace.load(decision.target_run_id)
        orchestrator = self._orchestrator()
        active = TeamActiveRun(run_id=run.id, user_task=run.user_task)
        self._register_started(active)
        try:
            outcome = await orchestrator.resume(run)
        except TeamProviderError as exc:
            failed_run = orchestrator.runs[-1] if orchestrator.runs else run
            self._apply_run_metadata(failed_run, {"resumed": True})
            workspace_path = self._save(failed_run)
            self._register_failed(failed_run, workspace_path=workspace_path)
            self.runs.append(failed_run)
            return self._response(
                decision=decision,
                status=TeamRuntimeStatus.FAILED,
                run_id=failed_run.id,
                workspace_path=workspace_path,
                user_visible_reply=f"Resume завершился с ошибкой: {exc}",
            )

        self._apply_run_metadata(outcome.run, {"resumed": True})
        workspace_path = self._save(outcome.run)
        self._register_completed(outcome.run, workspace_path=workspace_path)
        self.runs.append(outcome.run)
        return self._response(
            decision=decision,
            status=TeamRuntimeStatus.COMPLETED,
            run_id=outcome.run.id,
            final_text=outcome.final_text,
            workspace_path=workspace_path,
            outcome=outcome,
            user_visible_reply=outcome.final_text,
        )

    def _status_response(self, decision: TeamIntakeDecision) -> TeamRuntimeResponse:
        active = next(iter(self.state.active_runs.values()), None)
        active_text = "есть" if active is not None else "нет"
        current_worker = active.current_worker if active is not None else "никто"
        run_id = active.run_id if active is not None else self.state.last_run_id
        workspace_path = (
            active.workspace_path
            if active is not None and active.workspace_path is not None
            else self.state.last_workspace_path
        )
        lines = [
            f"Активная задача: {active_text}.",
            f"Кто работает: {current_worker}.",
            f"run_id: {run_id or 'нет'}",
            f"workspace: {workspace_path or 'нет'}",
        ]
        if self.state.last_completed_run_id:
            lines.append(f"Последний результат: {self.state.last_result_preview or 'нет'}")
            response_run_id = self.state.last_completed_run_id
        elif self.state.last_failed_run_id:
            lines.append("Последний результат: задача завершилась с ошибкой.")
            response_run_id = self.state.last_failed_run_id
        else:
            lines.append("Последний результат: пока нет.")
            response_run_id = run_id
        return self._response(
            decision=decision,
            run_id=response_run_id,
            workspace_path=workspace_path,
            user_visible_reply="\n".join(lines),
        )

    def _stop_all(self, decision: TeamIntakeDecision) -> TeamRuntimeResponse:
        stopped_ids = list(self.state.active_runs)
        for active in list(self.state.active_runs.values()):
            active.request_stop(decision.reason)
            self.state.stopped_runs[active.run_id] = active
        self.state.active_runs.clear()
        self.state.touch()
        if stopped_ids:
            reply = "Остановил активную задачу. Команда вернулась в общий чат."
        else:
            reply = "Активных задач сейчас нет."
        return self._response(
            decision=decision,
            status=TeamRuntimeStatus.CANCELLED,
            user_visible_reply=reply,
        )

    def _runtime_task_text(self, *, team_input: TeamInput, decision: TeamIntakeDecision) -> str:
        text = team_input.text.strip()
        if decision.intent == TeamInputIntent.TASK_FOLLOWUP:
            target = decision.target_run_id or team_input.active_run_id or "active"
            return f"Уточнение к run {target}: {text}"
        if decision.intent == TeamInputIntent.REVISE_PREVIOUS_RESULT:
            target = decision.target_run_id or team_input.last_run_id or "last"
            return f"Правка результата run {target}: {text}"
        if decision.intent == TeamInputIntent.FILE_TASK and team_input.attachments_count > 0:
            task_text = text or "Проверь приложенные файлы."
            return f"Задача по файлам ({team_input.attachments_count}): {task_text}"
        return text

    def _team_input(self, incoming: TeamInput | str, **metadata: Any) -> TeamInput:
        if isinstance(incoming, TeamInput):
            return incoming
        return TeamInput(text=incoming, **metadata)

    def _orchestrator(self) -> AsyncTeamOrchestrator:
        if self.orchestrator_factory is not None:
            return self.orchestrator_factory(self.provider)
        return AsyncTeamOrchestrator(provider=self.provider, message_sink=self.message_sink)

    def _save(self, run: TeamRun) -> Path | None:
        if self.workspace is None:
            return None
        return self.workspace.save(run)

    def _apply_run_metadata(self, run: TeamRun, metadata: dict[str, Any]) -> None:
        clean_metadata = {key: value for key, value in metadata.items() if value is not None}
        clean_metadata.setdefault("provider", self.provider.name)
        clean_metadata.setdefault("execution_mode", str(run.execution_mode))
        run.runtime_metadata.update(clean_metadata)

    def _register_started(self, active: TeamActiveRun) -> None:
        self.state.active_runs[active.run_id] = active
        self.state.last_run_id = active.run_id
        self.state.touch()

    def _register_completed(self, run: TeamRun, *, workspace_path: Path | None) -> None:
        self.state.active_runs.pop(run.id, None)
        self.state.last_run_id = run.id
        self.state.last_completed_run_id = run.id
        self.state.last_workspace_path = workspace_path
        self.state.last_result_preview = _preview_text(run.final_text or "")
        self.state.touch()
        if run.id in self.state.stopped_runs:
            self.state.stopped_runs[run.id].workspace_path = workspace_path

    def _register_failed(self, run: TeamRun, *, workspace_path: Path | None) -> None:
        self.state.active_runs.pop(run.id, None)
        self.state.last_run_id = run.id
        self.state.last_failed_run_id = run.id
        self.state.last_workspace_path = workspace_path
        self.state.last_result_preview = None
        self.state.touch()
        if run.status != RunStatus.FAILED:
            run.status = RunStatus.FAILED

    def _response(
        self,
        *,
        decision: TeamIntakeDecision,
        status: TeamRuntimeStatus = TeamRuntimeStatus.IDLE,
        run_id: str | None = None,
        final_text: str | None = None,
        workspace_path: Path | None = None,
        outcome: TeamRunOutcome | None = None,
        user_visible_reply: str | None = None,
    ) -> TeamRuntimeResponse:
        return TeamRuntimeResponse(
            user_visible_reply=user_visible_reply or decision.user_visible_reply,
            decision=decision,
            status=status,
            run_id=run_id,
            final_text=final_text,
            workspace_path=workspace_path,
            outcome=outcome,
            state=self.state,
        )


def _preview_text(text: str, *, limit: int = 180) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1].rstrip()}..."
