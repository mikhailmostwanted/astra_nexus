from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from astra_nexus.team.messages import (
    NullTeamMessageSink,
    TeamMessageRenderer,
    TeamMessageSink,
)
from astra_nexus.team.models import (
    AgentProfile,
    AgentResult,
    AgentRole,
    AgentTask,
    AgentTaskStatus,
    RunEvent,
    RunEventType,
    RunStatus,
    TeamRun,
    TeamRunOutcome,
    utc_now,
)
from astra_nexus.team.profiles import DEFAULT_AGENT_PIPELINE, default_profiles_by_role
from astra_nexus.team.prompting import AgentContext, AgentPrompt, TeamPromptBuilder
from astra_nexus.team.provider import TeamErrorKind, TeamProvider, TeamProviderError

TRANSIENT_PROVIDER_ERROR_CODES = {
    "response_timeout",
    "browser_connect_failed",
    "prompt_insert_failed",
    "chatgpt_ui_not_ready",
    "unavailable",
    "provider_error",
}

PERMANENT_PROVIDER_ERROR_CODES = {
    "login_required",
    "profile_locked",
    "prompt_box_not_found",
    "selector_not_found",
}


@dataclass(frozen=True)
class TeamRetryPolicy:
    max_retries: int = 0
    retry_delay_seconds: float = 0.0
    response_timeout_seconds: float | None = None


AGENT_STARTED_MESSAGES = {
    AgentRole.COORDINATOR: "Координатор начал разбирать задачу.",
    AgentRole.ANALYST: "Аналитик разбирает факты и вводные.",
    AgentRole.CRITIC: "Критик проверяет слабые места.",
    AgentRole.EDITOR: "Редактор собирает улучшенную версию.",
    AgentRole.QA_CONTROLLER: "Контроль качества проверяет результат.",
    AgentRole.FINAL_COMPOSER: "Финальный сборщик готовит ответ.",
}


AGENT_FINISHED_MESSAGES = {
    AgentRole.COORDINATOR: "Координатор подготовил план для команды.",
    AgentRole.ANALYST: "Аналитик подготовил разбор вводных.",
    AgentRole.CRITIC: "Критик сформулировал замечания.",
    AgentRole.EDITOR: "Редактор подготовил улучшенную версию.",
    AgentRole.QA_CONTROLLER: "Контроль качества завершил проверку.",
    AgentRole.FINAL_COMPOSER: "Финальный сборщик подготовил ответ.",
}


class AsyncTeamOrchestrator:
    def __init__(
        self,
        *,
        provider: TeamProvider,
        profiles: Sequence[AgentProfile] | None = None,
        pipeline: Sequence[AgentRole] | None = None,
        prompt_builder: TeamPromptBuilder | None = None,
        workspace_path: Path | str | None = None,
        extra_instructions: Sequence[str] | None = None,
        retry_policy: TeamRetryPolicy | None = None,
        message_sink: TeamMessageSink | None = None,
        message_renderer: TeamMessageRenderer | None = None,
    ) -> None:
        self.provider = provider
        self.pipeline = list(pipeline or DEFAULT_AGENT_PIPELINE)
        self.prompt_builder = prompt_builder or TeamPromptBuilder()
        self.workspace_path = Path(workspace_path) if workspace_path is not None else None
        self.extra_instructions = tuple(extra_instructions or ())
        self.retry_policy = retry_policy or TeamRetryPolicy()
        self.message_sink = message_sink or NullTeamMessageSink()
        self.profiles_by_role = default_profiles_by_role()
        if profiles is not None:
            self.profiles_by_role.update({profile.role: profile for profile in profiles})
        self.message_renderer = message_renderer or TeamMessageRenderer(self.profiles_by_role)
        self.runs: list[TeamRun] = []

    async def run(self, user_task: str) -> TeamRunOutcome:
        team_run = TeamRun(user_task=user_task)
        self.runs.append(team_run)
        self._start_run(team_run)
        return await self._execute_run(team_run)

    async def resume(self, team_run: TeamRun) -> TeamRunOutcome:
        self.runs.append(team_run)
        team_run.status = RunStatus.RUNNING
        team_run.completed_at = None
        self._append_event(
            team_run,
            RunEventType.RUN_STARTED,
            "Командный run продолжен.",
            payload={"status": team_run.status.value, "resumed": True},
        )
        return await self._execute_run(team_run)

    async def _execute_run(self, team_run: TeamRun) -> TeamRunOutcome:
        try:
            for role in self.pipeline:
                if self._role_completed(team_run, role):
                    continue
                await self._run_agent(
                    team_run=team_run,
                    profile=self.profiles_by_role[role],
                    agent_task=self._task_for_role(team_run, role),
                )
        except TeamProviderError:
            raise

        final_text = team_run.results[-1].content if team_run.results else ""
        team_run.final_text = final_text
        team_run.status = RunStatus.COMPLETED
        team_run.error_message = None
        team_run.completed_at = utc_now()
        self._append_event(
            team_run,
            RunEventType.RUN_FINISHED,
            "Командный run завершён.",
            payload={"status": team_run.status.value, "final_result": final_text},
        )
        return TeamRunOutcome(run=team_run, final_text=final_text)

    def _start_run(self, team_run: TeamRun) -> None:
        team_run.status = RunStatus.RUNNING
        team_run.started_at = utc_now()
        self._append_event(
            team_run,
            RunEventType.RUN_STARTED,
            "Командный run начат.",
            payload={"status": team_run.status.value},
        )

    async def _run_agent(
        self,
        *,
        team_run: TeamRun,
        profile: AgentProfile,
        agent_task: AgentTask | None = None,
    ) -> None:
        agent_task = agent_task or AgentTask(
            run_id=team_run.id,
            profile=profile,
            user_task=team_run.user_task,
        )
        if agent_task not in team_run.tasks:
            team_run.tasks.append(agent_task)
        agent_task.status = AgentTaskStatus.RUNNING
        agent_task.started_at = utc_now()
        agent_task.completed_at = None
        agent_task.error_message = None
        self._append_agent_event(
            team_run,
            RunEventType.AGENT_STARTED,
            profile=profile,
            agent_task=agent_task,
            message=AGENT_STARTED_MESSAGES[profile.role],
        )

        attempt_number = 1
        while True:
            if attempt_number > 1:
                agent_task.status = AgentTaskStatus.RUNNING
                self._append_agent_event(
                    team_run,
                    RunEventType.AGENT_RETRY_STARTED,
                    profile=profile,
                    agent_task=agent_task,
                    message=f"Агент {profile.role.value} начал повторную попытку.",
                    payload={
                        "attempt_number": attempt_number,
                        "retry_number": attempt_number - 1,
                        "max_retries": self.retry_policy.max_retries,
                    },
                )

            previous_results = tuple(team_run.results)
            try:
                prompt = self._build_prompt(
                    team_run=team_run,
                    profile=profile,
                    previous_results=previous_results,
                )
            except Exception as exc:
                provider_error = TeamProviderError(
                    "ошибка подготовки prompt для агента",
                    agent_id=profile.profile_id,
                    error_code="prompt_build_failed",
                    error_kind=TeamErrorKind.ORCHESTRATION_INTERNAL,
                    original_error=exc,
                )
                self._fail_agent_run(team_run=team_run, agent_task=agent_task, exc=provider_error)
                raise provider_error from exc

            try:
                content = await self._generate_with_timeout(
                    profile=profile,
                    user_task=team_run.user_task,
                    previous_results=previous_results,
                    prompt=prompt,
                )
                break
            except Exception as exc:
                provider_error = self._provider_error(exc, profile=profile)
                if self._can_retry(provider_error, attempt_number=attempt_number):
                    retry_number = attempt_number
                    self._append_agent_event(
                        team_run,
                        RunEventType.AGENT_RETRY_SCHEDULED,
                        profile=profile,
                        agent_task=agent_task,
                        message=f"Агент {profile.role.value} будет повторён.",
                        payload={
                            "attempt_number": attempt_number,
                            "retry_number": retry_number,
                            "next_attempt_number": attempt_number + 1,
                            "max_retries": self.retry_policy.max_retries,
                            "retry_delay_seconds": self.retry_policy.retry_delay_seconds,
                            "error": str(provider_error),
                            "error_code": provider_error.error_code,
                            "error_kind": provider_error.error_kind.value,
                        },
                    )
                    if self.retry_policy.retry_delay_seconds > 0:
                        await asyncio.sleep(self.retry_policy.retry_delay_seconds)
                    attempt_number += 1
                    continue

                self._fail_agent_run(team_run=team_run, agent_task=agent_task, exc=provider_error)
                raise provider_error from exc

        result = AgentResult(
            run_id=team_run.id,
            task_id=agent_task.id,
            profile=profile,
            content=content,
            metadata={"provider": self.provider.name, "prompt": prompt.as_dict()},
        )
        team_run.results.append(result)
        agent_task.status = AgentTaskStatus.COMPLETED
        agent_task.completed_at = utc_now()
        self._append_agent_event(
            team_run,
            RunEventType.AGENT_FINISHED,
            profile=profile,
            agent_task=agent_task,
            message=AGENT_FINISHED_MESSAGES[profile.role],
            payload={"result_id": result.id, "status": agent_task.status.value},
        )

    def _build_prompt(
        self,
        *,
        team_run: TeamRun,
        profile: AgentProfile,
        previous_results: Sequence[AgentResult],
    ) -> AgentPrompt:
        return self.prompt_builder.build(
            profile=profile,
            context=AgentContext(
                run_id=team_run.id,
                user_task=team_run.user_task,
                current_agent_role=profile.role,
                current_agent_name=profile.display_name,
                previous_results=previous_results,
                previous_events=tuple(team_run.events),
                workspace_path=self.workspace_path,
                extra_instructions=self.extra_instructions,
            ),
        )

    async def _generate_with_timeout(
        self,
        *,
        profile: AgentProfile,
        user_task: str,
        previous_results: Sequence[AgentResult],
        prompt: AgentPrompt,
    ) -> str:
        generate = self.provider.generate(
            profile=profile,
            user_task=user_task,
            previous_results=previous_results,
            prompt=prompt,
        )
        timeout = self.retry_policy.response_timeout_seconds
        if timeout is not None and timeout > 0:
            return await asyncio.wait_for(generate, timeout=timeout)
        return await generate

    def _provider_error(self, exc: Exception, *, profile: AgentProfile) -> TeamProviderError:
        if isinstance(exc, TeamProviderError):
            return exc
        if isinstance(exc, TimeoutError):
            return TeamProviderError(
                "истекло время ожидания ответа provider-а",
                agent_id=profile.profile_id,
                error_code="response_timeout",
                error_kind=TeamErrorKind.TRANSIENT_PROVIDER,
                original_error=exc,
            )

        error_code = str(
            getattr(exc, "status", None) or getattr(exc, "error_code", None) or "provider_error"
        )
        return TeamProviderError(
            str(exc),
            agent_id=profile.profile_id,
            error_code=error_code,
            error_kind=self._error_kind(error_code),
            original_error=exc,
        )

    def _error_kind(self, error_code: str) -> TeamErrorKind:
        if error_code in PERMANENT_PROVIDER_ERROR_CODES:
            return TeamErrorKind.PERMANENT_PROVIDER
        if error_code in TRANSIENT_PROVIDER_ERROR_CODES:
            return TeamErrorKind.TRANSIENT_PROVIDER
        return TeamErrorKind.TRANSIENT_PROVIDER

    def _can_retry(self, exc: TeamProviderError, *, attempt_number: int) -> bool:
        retries_used = attempt_number - 1
        return exc.transient and retries_used < self.retry_policy.max_retries

    def _fail_agent_run(
        self,
        *,
        team_run: TeamRun,
        agent_task: AgentTask,
        exc: TeamProviderError,
    ) -> None:
        error_message = str(exc)
        agent_task.status = AgentTaskStatus.FAILED
        agent_task.completed_at = utc_now()
        agent_task.error_message = error_message
        team_run.status = RunStatus.FAILED
        team_run.completed_at = utc_now()
        team_run.error_message = error_message
        self._append_agent_event(
            team_run,
            RunEventType.AGENT_FAILED,
            profile=agent_task.profile,
            agent_task=agent_task,
            message=f"Агент {agent_task.profile.role.value} завершился с ошибкой.",
            payload={
                "status": agent_task.status.value,
                "error": error_message,
                "error_code": exc.error_code,
                "error_kind": exc.error_kind.value,
                "transient": exc.transient,
            },
        )
        self._append_event(
            team_run,
            RunEventType.RUN_FAILED,
            "Командный run завершился с ошибкой.",
            payload={
                "status": team_run.status.value,
                "agent_role": agent_task.profile.role.value,
                "error": error_message,
                "error_code": exc.error_code,
                "error_kind": exc.error_kind.value,
                "transient": exc.transient,
            },
        )

    def _append_agent_event(
        self,
        team_run: TeamRun,
        event_type: RunEventType,
        *,
        profile: AgentProfile,
        agent_task: AgentTask,
        message: str,
        payload: dict | None = None,
    ) -> None:
        event_payload = {
            "role": profile.role.value,
            "agent_id": profile.profile_id,
            "task_id": agent_task.id,
            **(payload or {}),
        }
        self._append_event(
            team_run,
            event_type,
            message,
            agent_role=profile.role,
            agent_task_id=agent_task.id,
            payload=event_payload,
        )

    def _append_event(
        self,
        team_run: TeamRun,
        event_type: RunEventType,
        message: str,
        *,
        agent_role: AgentRole | None = None,
        agent_task_id: str | None = None,
        payload: dict | None = None,
    ) -> None:
        event = RunEvent(
            run_id=team_run.id,
            type=event_type,
            message=message,
            agent_role=agent_role,
            agent_task_id=agent_task_id,
            payload=payload or {},
        )
        team_run.events.append(event)
        self._emit_event_messages(team_run, event)

    def _emit_event_messages(self, team_run: TeamRun, event: RunEvent) -> None:
        for message in self.message_renderer.render_event(event):
            team_run.messages.append(message)
            self.message_sink.publish(message)

    def _role_completed(self, team_run: TeamRun, role: AgentRole) -> bool:
        return any(result.profile.role == role for result in team_run.results)

    def _task_for_role(self, team_run: TeamRun, role: AgentRole) -> AgentTask | None:
        for task in reversed(team_run.tasks):
            if task.profile.role == role:
                return task
        return None
