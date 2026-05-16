from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from astra_nexus.team.models import AgentRole, TeamRun


class TeamExecutionMode(StrEnum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


@dataclass(frozen=True)
class TeamExecutionDependency:
    role: AgentRole
    depends_on: AgentRole


@dataclass(frozen=True)
class TeamExecutionStep:
    id: str
    roles: tuple[AgentRole, ...]
    mode: TeamExecutionMode = TeamExecutionMode.SEQUENTIAL
    dependencies: tuple[TeamExecutionDependency, ...] = ()

    def dependencies_for(self, role: AgentRole) -> tuple[AgentRole, ...]:
        return tuple(
            dependency.depends_on for dependency in self.dependencies if dependency.role == role
        )


@dataclass(frozen=True)
class TeamExecutionPlan:
    mode: TeamExecutionMode
    steps: tuple[TeamExecutionStep, ...]
    max_parallel_agents: int = 2
    parallel_agent_timeout_seconds: float | None = 240.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def dependencies_for(self, role: AgentRole) -> tuple[AgentRole, ...]:
        for step in self.steps:
            if role in step.roles:
                return step.dependencies_for(role)
        return ()

    def with_limits(
        self,
        *,
        max_parallel_agents: int,
        parallel_agent_timeout_seconds: float | None,
    ) -> TeamExecutionPlan:
        return replace(
            self,
            max_parallel_agents=max(1, max_parallel_agents),
            parallel_agent_timeout_seconds=parallel_agent_timeout_seconds,
        )


def default_sequential_execution_plan(
    pipeline: tuple[AgentRole, ...] | list[AgentRole],
    *,
    max_parallel_agents: int = 1,
    parallel_agent_timeout_seconds: float | None = None,
) -> TeamExecutionPlan:
    roles = tuple(pipeline)
    steps = []
    previous_role: AgentRole | None = None
    for index, role in enumerate(roles, start=1):
        dependencies = (
            (TeamExecutionDependency(role=role, depends_on=previous_role),)
            if previous_role is not None
            else ()
        )
        steps.append(
            TeamExecutionStep(
                id=f"step_{index:02d}_{role.value}",
                roles=(role,),
                mode=TeamExecutionMode.SEQUENTIAL,
                dependencies=dependencies,
            )
        )
        previous_role = role
    return TeamExecutionPlan(
        mode=TeamExecutionMode.SEQUENTIAL,
        steps=tuple(steps),
        max_parallel_agents=max_parallel_agents,
        parallel_agent_timeout_seconds=parallel_agent_timeout_seconds,
        metadata={"strategy": "pipeline_order"},
    )


def default_parallel_execution_plan(
    *,
    max_parallel_agents: int = 2,
    parallel_agent_timeout_seconds: float | None = 240.0,
) -> TeamExecutionPlan:
    return TeamExecutionPlan(
        mode=TeamExecutionMode.PARALLEL,
        steps=(
            TeamExecutionStep(
                id="step_01_coordination",
                roles=(AgentRole.COORDINATOR,),
                mode=TeamExecutionMode.SEQUENTIAL,
            ),
            TeamExecutionStep(
                id="step_02_analysis_and_risk_check",
                roles=(AgentRole.ANALYST, AgentRole.CRITIC),
                mode=TeamExecutionMode.PARALLEL,
                dependencies=(
                    TeamExecutionDependency(
                        role=AgentRole.ANALYST,
                        depends_on=AgentRole.COORDINATOR,
                    ),
                    TeamExecutionDependency(
                        role=AgentRole.CRITIC,
                        depends_on=AgentRole.COORDINATOR,
                    ),
                ),
            ),
            TeamExecutionStep(
                id="step_03_revision",
                roles=(AgentRole.EDITOR,),
                mode=TeamExecutionMode.SEQUENTIAL,
                dependencies=(
                    TeamExecutionDependency(
                        role=AgentRole.EDITOR,
                        depends_on=AgentRole.COORDINATOR,
                    ),
                    TeamExecutionDependency(
                        role=AgentRole.EDITOR,
                        depends_on=AgentRole.ANALYST,
                    ),
                    TeamExecutionDependency(
                        role=AgentRole.EDITOR,
                        depends_on=AgentRole.CRITIC,
                    ),
                ),
            ),
            TeamExecutionStep(
                id="step_04_qa",
                roles=(AgentRole.QA_CONTROLLER,),
                mode=TeamExecutionMode.SEQUENTIAL,
                dependencies=(
                    TeamExecutionDependency(
                        role=AgentRole.QA_CONTROLLER,
                        depends_on=AgentRole.EDITOR,
                    ),
                ),
            ),
            TeamExecutionStep(
                id="step_05_final",
                roles=(AgentRole.FINAL_COMPOSER,),
                mode=TeamExecutionMode.SEQUENTIAL,
                dependencies=(
                    TeamExecutionDependency(
                        role=AgentRole.FINAL_COMPOSER,
                        depends_on=AgentRole.QA_CONTROLLER,
                    ),
                ),
            ),
        ),
        max_parallel_agents=max(1, max_parallel_agents),
        parallel_agent_timeout_seconds=parallel_agent_timeout_seconds,
        metadata={
            "strategy": "parallel_foundation_v1",
            "critic_scope": "preliminary_risk_check_after_coordinator",
        },
    )


def execution_plan_for_mode(
    mode: TeamExecutionMode | str,
    *,
    pipeline: tuple[AgentRole, ...] | list[AgentRole],
    max_parallel_agents: int = 2,
    parallel_agent_timeout_seconds: float | None = 240.0,
    intent: str | None = None,
) -> TeamExecutionPlan:
    if intent:
        from astra_nexus.team.intake import TeamInputIntent

        if intent == TeamInputIntent.SIMPLE_ANSWER:
            return default_sequential_execution_plan(
                [AgentRole.FINAL_COMPOSER], metadata={"strategy": "simple_answer_intent"}
            )
        if intent == TeamInputIntent.FILE_GENERATION:
            return default_sequential_execution_plan(
                [AgentRole.ANALYST, AgentRole.FINAL_COMPOSER],
                metadata={"strategy": "file_generation_intent"},
            )
        if intent == TeamInputIntent.FILE_TASK:
            return default_sequential_execution_plan(
                [AgentRole.ANALYST, AgentRole.EDITOR, AgentRole.FINAL_COMPOSER],
                metadata={"strategy": "file_task_intent"},
            )
        if intent == TeamInputIntent.DEBUG_MODE:
            return default_sequential_execution_plan(
                [
                    AgentRole.ANALYST,
                    AgentRole.CRITIC,
                    AgentRole.QA_CONTROLLER,
                    AgentRole.FINAL_COMPOSER,
                ],
                metadata={"strategy": "debug_mode_intent"},
            )

    normalized = TeamExecutionMode(mode)
    if normalized == TeamExecutionMode.PARALLEL:
        return default_parallel_execution_plan(
            max_parallel_agents=max_parallel_agents,
            parallel_agent_timeout_seconds=parallel_agent_timeout_seconds,
        )
    return default_sequential_execution_plan(
        pipeline,
        max_parallel_agents=1,
        parallel_agent_timeout_seconds=None,
    )


def execution_plan_payload(plan: TeamExecutionPlan) -> dict[str, Any]:
    return {
        "mode": plan.mode.value,
        "max_parallel_agents": plan.max_parallel_agents,
        "parallel_agent_timeout_seconds": plan.parallel_agent_timeout_seconds,
        "metadata": plan.metadata,
        "steps": [
            {
                "id": step.id,
                "mode": step.mode.value,
                "roles": [role.value for role in step.roles],
                "dependencies": [
                    {
                        "role": dependency.role.value,
                        "depends_on": dependency.depends_on.value,
                    }
                    for dependency in step.dependencies
                ],
            }
            for step in plan.steps
        ],
    }


def execution_plan_from_payload(payload: dict[str, Any]) -> TeamExecutionPlan:
    return TeamExecutionPlan(
        mode=TeamExecutionMode(payload["mode"]),
        max_parallel_agents=payload.get("max_parallel_agents", 1),
        parallel_agent_timeout_seconds=payload.get("parallel_agent_timeout_seconds"),
        metadata=payload.get("metadata", {}),
        steps=tuple(
            TeamExecutionStep(
                id=step["id"],
                mode=TeamExecutionMode(step["mode"]),
                roles=tuple(AgentRole(role) for role in step["roles"]),
                dependencies=tuple(
                    TeamExecutionDependency(
                        role=AgentRole(dependency["role"]),
                        depends_on=AgentRole(dependency["depends_on"]),
                    )
                    for dependency in step.get("dependencies", [])
                ),
            )
            for step in payload.get("steps", [])
        ),
    )


def execution_timeline_markdown(run: TeamRun) -> str:
    plan = run.execution_plan
    sections = ["# Execution Timeline", "", f"Execution mode: `{run.execution_mode}`", ""]
    if plan is None:
        sections.extend(["No execution plan saved.", ""])
        return "\n".join(sections)

    sections.extend(["## Parallel Steps", ""])
    for step in plan.steps:
        roles = ", ".join(role.value for role in step.roles)
        sections.append(f"- `{step.id}` `{step.mode.value}`: {roles}")
    sections.append("")
    sections.extend(["## Agent Tasks", ""])
    for task in run.tasks:
        dependencies = ", ".join(role.value for role in task.dependencies) or "none"
        sections.append(
            "- "
            f"`{task.profile.role.value}` "
            f"step=`{task.execution_step_id or ''}` "
            f"status=`{task.status.value}` "
            f"dependencies=`{dependencies}` "
            f"started=`{task.started_at.isoformat() if task.started_at else ''}` "
            f"finished=`{task.completed_at.isoformat() if task.completed_at else ''}`"
        )
    sections.append("")
    return "\n".join(sections)
