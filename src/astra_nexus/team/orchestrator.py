from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astra_nexus.team.attachments import TeamInputAttachment
from astra_nexus.team.dialogue import (
    TeamDialogueTurn,
    build_agent_finish_turn,
    build_agent_start_turn,
    build_completed_turn,
    build_failed_turn,
    dialogue_turn_to_messages,
)
from astra_nexus.team.execution_plan import (
    TeamExecutionMode,
    TeamExecutionPlan,
    TeamExecutionStep,
    default_sequential_execution_plan,
    execution_plan_for_mode,
)
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
from astra_nexus.team.provider import (
    TeamErrorKind,
    TeamProvider,
    TeamProviderError,
    TeamProviderOutput,
)
from astra_nexus.team.review_protocol import (
    build_final_package,
    build_quality_criteria,
    build_task_brief,
    review_decision_from_qa_result,
    review_notes_from_critic_result,
    revision_requests_from_notes,
)

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
        execution_mode: TeamExecutionMode | str = TeamExecutionMode.SEQUENTIAL,
        execution_plan: TeamExecutionPlan | None = None,
        max_parallel_agents: int = 2,
        parallel_agent_timeout_seconds: float | None = 240.0,
        max_revision_loops: int = 1,
    ) -> None:
        self.provider = provider
        self.pipeline = list(pipeline or DEFAULT_AGENT_PIPELINE)
        self.requested_execution_mode = TeamExecutionMode(execution_mode)
        self.requested_execution_plan = execution_plan
        self.max_parallel_agents = max(1, max_parallel_agents)
        self.parallel_agent_timeout_seconds = parallel_agent_timeout_seconds
        self.max_revision_loops = max(0, max_revision_loops)
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

    async def run(
        self,
        user_task: str,
        *,
        attachments: Sequence[TeamInputAttachment] = (),
        runtime_metadata: dict[str, Any] | None = None,
        intent: str | None = None,
    ) -> TeamRunOutcome:
        team_run = TeamRun(user_task=user_task, attachments=list(attachments))
        if intent:
            team_run.runtime_metadata["intent"] = intent
        if runtime_metadata:
            team_run.runtime_metadata.update(
                {key: value for key, value in runtime_metadata.items() if value is not None}
            )
        self._assign_execution_plan(team_run)
        self._initialize_review_protocol(team_run)
        self.runs.append(team_run)
        self._start_run(team_run)
        return await self._execute_run(team_run)

    async def resume(self, team_run: TeamRun) -> TeamRunOutcome:
        self.runs.append(team_run)
        team_run.status = RunStatus.RUNNING
        team_run.completed_at = None
        self._assign_execution_plan(team_run)
        self._initialize_review_protocol(team_run)
        self._append_event(
            team_run,
            RunEventType.RUN_STARTED,
            "Командный run продолжен.",
            payload={
                "status": team_run.status.value,
                "resumed": True,
                "provider": self.provider.name,
                "execution_mode": team_run.execution_mode.value,
                "workspace": str(self.workspace_path) if self.workspace_path is not None else None,
            },
        )
        return await self._execute_run(team_run)

    async def _execute_run(self, team_run: TeamRun) -> TeamRunOutcome:
        try:
            await self._execute_plan(team_run)
        except TeamProviderError:
            raise
        except asyncio.CancelledError:
            self._cancel_run(team_run, reason="cancelled")
            raise

        final_text = team_run.results[-1].content if team_run.results else ""
        team_run.final_text = final_text
        team_run.final_package = build_final_package(
            final_text=final_text,
            brief=team_run.task_brief,
            decision=team_run.review_decision,
            applied_revision_count=team_run.revision_loops_count,
        )
        team_run.status = RunStatus.COMPLETED
        team_run.error_message = None
        team_run.completed_at = utc_now()
        self._append_event(
            team_run,
            RunEventType.RUN_FINISHED,
            "Командный run завершён.",
            payload={
                "status": team_run.status.value,
                "final_result": final_text,
                "provider": self.provider.name,
                "execution_mode": team_run.execution_mode.value,
                "workspace": str(self.workspace_path) if self.workspace_path is not None else None,
            },
        )
        self._append_dialogue_turn(team_run, build_completed_turn(run_id=team_run.id))
        return TeamRunOutcome(run=team_run, final_text=final_text)

    def _start_run(self, team_run: TeamRun) -> None:
        team_run.status = RunStatus.RUNNING
        team_run.started_at = utc_now()
        self._append_event(
            team_run,
            RunEventType.RUN_STARTED,
            "Командный run начат.",
            payload={
                "status": team_run.status.value,
                "provider": self.provider.name,
                "execution_mode": team_run.execution_mode.value,
                "workspace": str(self.workspace_path) if self.workspace_path is not None else None,
            },
        )

    def _assign_execution_plan(self, team_run: TeamRun) -> None:
        plan = self._effective_execution_plan(team_run)
        team_run.execution_mode = plan.mode
        team_run.execution_plan = plan

    def _initialize_review_protocol(self, team_run: TeamRun) -> None:
        if team_run.task_brief is None:
            team_run.task_brief = build_task_brief(
                original_user_input=team_run.user_task,
                attachments=tuple(team_run.attachments),
                created_by=AgentRole.COORDINATOR,
            )
        if not team_run.quality_criteria:
            team_run.quality_criteria = list(
                build_quality_criteria(source_agent=AgentRole.COORDINATOR)
            )

    def _effective_execution_plan(self, team_run: TeamRun | None = None) -> TeamExecutionPlan:
        intent = team_run.runtime_metadata.get("intent") if team_run else None
        requested = self.requested_execution_plan or execution_plan_for_mode(
            self.requested_execution_mode,
            pipeline=self.pipeline,
            max_parallel_agents=self.max_parallel_agents,
            parallel_agent_timeout_seconds=self.parallel_agent_timeout_seconds,
            intent=intent,
        )
        requested = requested.with_limits(
            max_parallel_agents=self.max_parallel_agents,
            parallel_agent_timeout_seconds=self.parallel_agent_timeout_seconds,
        )
        if requested.mode == TeamExecutionMode.PARALLEL and not self.provider.supports_parallel:
            return default_sequential_execution_plan(
                self.pipeline,
                max_parallel_agents=1,
                parallel_agent_timeout_seconds=None,
            )
        return requested

    async def _execute_plan(self, team_run: TeamRun) -> None:
        plan = team_run.execution_plan or self._effective_execution_plan()
        for step in plan.steps:
            if step.mode == TeamExecutionMode.PARALLEL and len(step.roles) > 1:
                await self._run_parallel_step(team_run=team_run, step=step, plan=plan)
            else:
                for role in step.roles:
                    if role == AgentRole.FINAL_COMPOSER:
                        await self._run_revision_loop_if_needed(team_run)
                    await self._run_planned_role(
                        team_run=team_run,
                        role=role,
                        step=step,
                    )
            self._sort_run_agent_state(team_run)

    async def _run_revision_loop_if_needed(self, team_run: TeamRun) -> None:
        while (
            team_run.review_decision is not None
            and team_run.review_decision.needs_revision
            and team_run.revision_loops_count < self.max_revision_loops
        ):
            loop_number = team_run.revision_loops_count + 1
            await self._run_agent(
                team_run=team_run,
                profile=self.profiles_by_role[AgentRole.EDITOR],
                dependencies=self._dependencies_for_role(team_run, AgentRole.EDITOR),
                execution_step_id=f"revision_loop_{loop_number:02d}_editor",
                execution_mode=TeamExecutionMode.SEQUENTIAL,
            )
            await self._run_agent(
                team_run=team_run,
                profile=self.profiles_by_role[AgentRole.QA_CONTROLLER],
                dependencies=self._dependencies_for_role(team_run, AgentRole.QA_CONTROLLER),
                execution_step_id=f"revision_loop_{loop_number:02d}_qa",
                execution_mode=TeamExecutionMode.SEQUENTIAL,
            )
            team_run.revision_loops_count = loop_number
            self._sort_run_agent_state(team_run)

    def _dependencies_for_role(self, team_run: TeamRun, role: AgentRole) -> tuple[AgentRole, ...]:
        if team_run.execution_plan is None:
            return ()
        return team_run.execution_plan.dependencies_for(role)

    async def _run_parallel_step(
        self,
        *,
        team_run: TeamRun,
        step: TeamExecutionStep,
        plan: TeamExecutionPlan,
    ) -> None:
        previous_results = tuple(team_run.results)
        semaphore = asyncio.Semaphore(plan.max_parallel_agents)

        async def run_role(role: AgentRole) -> None:
            async with semaphore:
                run_agent = self._run_planned_role(
                    team_run=team_run,
                    role=role,
                    step=step,
                    previous_results_override=previous_results,
                )
                timeout = plan.parallel_agent_timeout_seconds
                if timeout is not None and timeout > 0:
                    try:
                        await asyncio.wait_for(run_agent, timeout=timeout)
                    except TimeoutError as exc:
                        agent_task = self._task_for_role(team_run, role) or AgentTask(
                            run_id=team_run.id,
                            profile=self.profiles_by_role[role],
                            user_task=team_run.user_task,
                            dependencies=step.dependencies_for(role),
                            execution_step_id=step.id,
                            execution_mode=step.mode.value,
                        )
                        provider_error = TeamProviderError(
                            "истекло время ожидания parallel agent step",
                            agent_id=self.profiles_by_role[role].profile_id,
                            error_code="response_timeout",
                            error_kind=TeamErrorKind.TRANSIENT_PROVIDER,
                            original_error=exc,
                        )
                        self._fail_agent_run(
                            team_run=team_run,
                            agent_task=agent_task,
                            exc=provider_error,
                        )
                        raise provider_error from exc
                    return
                await run_agent

        tasks = [asyncio.create_task(run_role(role)) for role in step.roles]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._cancel_agent_tasks(team_run, roles=step.roles, reason="cancelled")
            raise
        except Exception:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._cancel_agent_tasks(team_run, roles=step.roles, reason="cancelled")
            raise

    async def _run_planned_role(
        self,
        *,
        team_run: TeamRun,
        role: AgentRole,
        step: TeamExecutionStep,
        previous_results_override: Sequence[AgentResult] | None = None,
    ) -> None:
        if self._role_completed(team_run, role):
            return
        await self._run_agent(
            team_run=team_run,
            profile=self.profiles_by_role[role],
            agent_task=self._task_for_role(team_run, role),
            dependencies=step.dependencies_for(role),
            execution_step_id=step.id,
            execution_mode=step.mode,
            previous_results_override=previous_results_override,
        )

    async def _run_agent(
        self,
        *,
        team_run: TeamRun,
        profile: AgentProfile,
        agent_task: AgentTask | None = None,
        dependencies: Sequence[AgentRole] = (),
        execution_step_id: str | None = None,
        execution_mode: TeamExecutionMode = TeamExecutionMode.SEQUENTIAL,
        previous_results_override: Sequence[AgentResult] | None = None,
    ) -> None:
        agent_task = agent_task or AgentTask(
            run_id=team_run.id,
            profile=profile,
            user_task=team_run.user_task,
        )
        if agent_task not in team_run.tasks:
            team_run.tasks.append(agent_task)
        agent_task.dependencies = tuple(dependencies)
        agent_task.execution_step_id = execution_step_id
        agent_task.execution_mode = execution_mode.value
        agent_task.status = AgentTaskStatus.RUNNING
        agent_task.started_at = utc_now()
        agent_task.completed_at = None
        agent_task.error_message = None
        self._append_dialogue_turn(
            team_run,
            build_agent_start_turn(
                run_id=team_run.id,
                profile=profile,
                attachments=team_run.attachments,
            ),
        )
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

            previous_results = (
                tuple(previous_results_override)
                if previous_results_override is not None
                else tuple(team_run.results)
            )
            try:
                prompt = self._build_prompt(
                    team_run=team_run,
                    profile=profile,
                    previous_results=previous_results,
                )
                prompt.metadata["execution_step_id"] = execution_step_id
                prompt.metadata["agent_task_id"] = agent_task.id
                prompt.metadata["attempt_number"] = attempt_number
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
                provider_output = await self._generate_with_timeout(
                    profile=profile,
                    user_task=team_run.user_task,
                    previous_results=previous_results,
                    prompt=prompt,
                )
                content, provider_metadata = self._normalize_provider_output(provider_output)
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
            metadata={
                "provider": self.provider.name,
                "prompt": prompt.as_dict(),
                "execution_step_id": execution_step_id,
                "execution_mode": execution_mode.value,
                "dependencies": [role.value for role in dependencies],
                "provider_response": provider_metadata,
            },
        )
        team_run.results.append(result)
        self._apply_review_protocol_result(team_run=team_run, result=result)
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
        self._append_dialogue_turn(
            team_run,
            build_agent_finish_turn(
                run_id=team_run.id,
                profile=profile,
                needs_revision=(
                    team_run.review_decision.needs_revision
                    if profile.role == AgentRole.QA_CONTROLLER
                    and team_run.review_decision is not None
                    else None
                ),
            ),
        )

    def _apply_review_protocol_result(self, *, team_run: TeamRun, result: AgentResult) -> None:
        if result.profile.role == AgentRole.CRITIC:
            notes = review_notes_from_critic_result(result.content)
            team_run.review_notes.extend(notes)
            team_run.revision_requests.extend(revision_requests_from_notes(notes))
            return

        if result.profile.role == AgentRole.QA_CONTROLLER:
            decision, qa_note, revision_request = review_decision_from_qa_result(
                result.content,
                existing_notes=tuple(team_run.review_notes),
            )
            team_run.review_decision = decision
            if qa_note is not None:
                team_run.review_notes.append(qa_note)
            if revision_request is not None:
                team_run.revision_requests.append(revision_request)

    def _build_prompt(
        self,
        *,
        team_run: TeamRun,
        profile: AgentProfile,
        previous_results: Sequence[AgentResult],
    ) -> AgentPrompt:
        extra_instructions = (
            *self.extra_instructions,
            *self._requested_file_extra_instructions(team_run=team_run, profile=profile),
        )
        prompt = self.prompt_builder.build(
            profile=profile,
            context=AgentContext(
                run_id=team_run.id,
                user_task=team_run.user_task,
                current_agent_role=profile.role,
                current_agent_name=profile.display_name,
                previous_results=previous_results,
                previous_events=tuple(team_run.events),
                attachments=tuple(team_run.attachments),
                task_brief=team_run.task_brief,
                quality_criteria=tuple(team_run.quality_criteria),
                review_notes=tuple(team_run.review_notes),
                revision_requests=tuple(team_run.revision_requests),
                review_decision=team_run.review_decision,
                workspace_path=self.workspace_path,
                extra_instructions=extra_instructions,
            ),
        )
        prompt.metadata.update(self._requested_file_prompt_metadata(team_run, profile=profile))
        prompt.metadata.update(self._input_artifacts_metadata(team_run))
        return prompt

    def _input_artifacts_metadata(self, team_run: TeamRun) -> dict[str, Any]:
        if not team_run.attachments:
            return {}
        return {
            "input_artifacts": [
                str(a.local_path) for a in team_run.attachments if a.local_path
            ]
        }

    def _requested_file_extra_instructions(
        self,
        *,
        team_run: TeamRun,
        profile: AgentProfile,
    ) -> tuple[str, ...]:
        if profile.role != AgentRole.FINAL_COMPOSER:
            return ()
        if not team_run.runtime_metadata.get("output_requested_as_file"):
            return ()
        output_format = (
            str(team_run.runtime_metadata.get("requested_output_format") or "file").strip().lower()
        )
        if output_format and not output_format.startswith("."):
            output_format = f".{output_format}"
        return (
            "Requested file delivery:",
            "Create an actual downloadable file in ChatGPT Web.",
            f"Required extension: {output_format or 'requested file format'}.",
            "Do not treat plain text as success when the user asked for a file.",
            "The final turn must expose a file card, attachment, filename chip, download link, "
            "or download button that can be clicked by the browser.",
            "Keep the accompanying text short; the downloadable file is the deliverable.",
        )

    def _requested_file_prompt_metadata(
        self,
        team_run: TeamRun,
        *,
        profile: AgentProfile,
    ) -> dict[str, Any]:
        if profile.role != AgentRole.FINAL_COMPOSER:
            return {}
        if not team_run.runtime_metadata.get("output_requested_as_file"):
            return {}
        return {
            "output_requested_as_file": True,
            "requested_output_format": str(
                team_run.runtime_metadata.get("requested_output_format") or "unknown"
            ),
        }

    async def _generate_with_timeout(
        self,
        *,
        profile: AgentProfile,
        user_task: str,
        previous_results: Sequence[AgentResult],
        prompt: AgentPrompt,
    ) -> str | TeamProviderOutput:
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

    def _normalize_provider_output(
        self,
        output: str | TeamProviderOutput | Any,
    ) -> tuple[str, dict[str, Any]]:
        if isinstance(output, TeamProviderOutput):
            return output.content, dict(output.metadata)
        content = getattr(output, "content", output)
        metadata = getattr(output, "metadata", {})
        return str(content), dict(metadata) if isinstance(metadata, dict) else {}

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
        self._append_dialogue_turn(
            team_run,
            build_failed_turn(run_id=team_run.id, profile=agent_task.profile),
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
                "provider": self.provider.name,
                "execution_mode": team_run.execution_mode.value,
                "workspace": str(self.workspace_path) if self.workspace_path is not None else None,
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
            "provider": self.provider.name,
            "execution_mode": agent_task.execution_mode,
            "execution_step_id": agent_task.execution_step_id,
            "workspace": str(self.workspace_path) if self.workspace_path is not None else None,
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

    def _append_dialogue_turn(self, team_run: TeamRun, turn: TeamDialogueTurn) -> None:
        team_run.dialogue_turns.append(turn)
        for message in dialogue_turn_to_messages(turn):
            team_run.messages.append(message)
            self.message_sink.publish(message)

    def _cancel_run(self, team_run: TeamRun, *, reason: str) -> None:
        if team_run.status == RunStatus.FAILED:
            return
        self._cancel_agent_tasks(
            team_run,
            roles=tuple(task.profile.role for task in team_run.tasks),
            reason=reason,
        )
        team_run.status = RunStatus.CANCELLED
        team_run.error_message = reason
        team_run.completed_at = utc_now()

    def _cancel_agent_tasks(
        self,
        team_run: TeamRun,
        *,
        roles: Sequence[AgentRole],
        reason: str,
    ) -> None:
        role_set = set(roles)
        for task in team_run.tasks:
            if task.profile.role not in role_set or task.status != AgentTaskStatus.RUNNING:
                continue
            task.status = AgentTaskStatus.FAILED
            task.completed_at = utc_now()
            task.error_message = reason

    def _sort_run_agent_state(self, team_run: TeamRun) -> None:
        order = {role: index for index, role in enumerate(self.pipeline)}
        team_run.tasks.sort(key=lambda task: order.get(task.profile.role, len(order)))
        team_run.results.sort(key=lambda result: order.get(result.profile.role, len(order)))

    def _role_completed(self, team_run: TeamRun, role: AgentRole) -> bool:
        return any(result.profile.role == role for result in team_run.results)

    def _task_for_role(self, team_run: TeamRun, role: AgentRole) -> AgentTask | None:
        for task in reversed(team_run.tasks):
            if task.profile.role == role:
                return task
        return None
