from __future__ import annotations

from collections.abc import Sequence

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
from astra_nexus.team.provider import TeamProvider, TeamProviderError


class AsyncTeamOrchestrator:
    def __init__(
        self,
        *,
        provider: TeamProvider,
        profiles: Sequence[AgentProfile] | None = None,
        pipeline: Sequence[AgentRole] | None = None,
    ) -> None:
        self.provider = provider
        self.pipeline = list(pipeline or DEFAULT_AGENT_PIPELINE)
        self.profiles_by_role = default_profiles_by_role()
        if profiles is not None:
            self.profiles_by_role.update({profile.role: profile for profile in profiles})
        self.runs: list[TeamRun] = []

    async def run(self, user_task: str) -> TeamRunOutcome:
        team_run = TeamRun(user_task=user_task)
        self.runs.append(team_run)
        self._start_run(team_run)

        try:
            for role in self.pipeline:
                await self._run_agent(team_run=team_run, profile=self.profiles_by_role[role])
        except TeamProviderError:
            raise

        final_text = team_run.results[-1].content if team_run.results else ""
        team_run.final_text = final_text
        team_run.status = RunStatus.COMPLETED
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

    async def _run_agent(self, *, team_run: TeamRun, profile: AgentProfile) -> None:
        agent_task = AgentTask(run_id=team_run.id, profile=profile, user_task=team_run.user_task)
        team_run.tasks.append(agent_task)
        agent_task.status = AgentTaskStatus.RUNNING
        agent_task.started_at = utc_now()
        self._append_agent_event(
            team_run,
            RunEventType.AGENT_STARTED,
            profile=profile,
            agent_task=agent_task,
            message=f"Агент {profile.role.value} начал работу.",
        )

        try:
            content = await self.provider.generate(
                profile=profile,
                user_task=team_run.user_task,
                previous_results=tuple(team_run.results),
            )
        except TeamProviderError as exc:
            self._fail_agent_run(team_run=team_run, agent_task=agent_task, exc=exc)
            raise
        except Exception as exc:
            provider_error = TeamProviderError(str(exc), agent_id=profile.profile_id)
            self._fail_agent_run(team_run=team_run, agent_task=agent_task, exc=provider_error)
            raise provider_error from exc

        result = AgentResult(
            run_id=team_run.id,
            task_id=agent_task.id,
            profile=profile,
            content=content,
            metadata={"provider": self.provider.name},
        )
        team_run.results.append(result)
        agent_task.status = AgentTaskStatus.COMPLETED
        agent_task.completed_at = utc_now()
        self._append_agent_event(
            team_run,
            RunEventType.AGENT_FINISHED,
            profile=profile,
            agent_task=agent_task,
            message=f"Агент {profile.role.value} завершил работу.",
            payload={"result_id": result.id, "status": agent_task.status.value},
        )

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
            payload={"status": agent_task.status.value, "error": error_message},
        )
        self._append_event(
            team_run,
            RunEventType.RUN_FAILED,
            "Командный run завершился с ошибкой.",
            payload={
                "status": team_run.status.value,
                "agent_role": agent_task.profile.role.value,
                "error": error_message,
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
        team_run.events.append(
            RunEvent(
                run_id=team_run.id,
                type=event_type,
                message=message,
                agent_role=agent_role,
                agent_task_id=agent_task_id,
                payload=payload or {},
            )
        )
