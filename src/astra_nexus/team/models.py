from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from astra_nexus.utils.ids import new_id

if TYPE_CHECKING:
    from astra_nexus.team.attachments import TeamInputAttachment
    from astra_nexus.team.dialogue import TeamDialogueTurn
    from astra_nexus.team.execution_plan import TeamExecutionPlan
    from astra_nexus.team.messages import TeamMessage
    from astra_nexus.team.review_protocol import (
        TeamFinalPackage,
        TeamQualityCriterion,
        TeamReviewDecision,
        TeamReviewNote,
        TeamRevisionRequest,
        TeamTaskBrief,
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentTaskStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RunEventType(StrEnum):
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    RUN_FAILED = "run_failed"
    AGENT_STARTED = "agent_started"
    AGENT_FINISHED = "agent_finished"
    AGENT_RETRY_SCHEDULED = "agent_retry_scheduled"
    AGENT_RETRY_STARTED = "agent_retry_started"
    AGENT_FAILED = "agent_failed"


class AgentRole(StrEnum):
    COORDINATOR = "coordinator"
    ANALYST = "analyst"
    CRITIC = "critic"
    EDITOR = "editor"
    QA_CONTROLLER = "qa_controller"
    FINAL_COMPOSER = "final_composer"


@dataclass(frozen=True)
class AgentProfile:
    role: AgentRole
    display_name: str
    description: str
    system_instruction: str
    short_name: str = ""
    short_description: str = ""
    style_hint: str = ""
    main_chat_intro: str = ""
    responsibility_summary: str = ""
    personality: str = ""
    capabilities: tuple[str, ...] = ()
    default_style: str = ""
    id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def profile_id(self) -> str:
        return self.id or self.role.value


@dataclass
class AgentTask:
    run_id: str
    profile: AgentProfile
    user_task: str
    id: str = field(default_factory=lambda: new_id("agent_task"))
    status: AgentTaskStatus = AgentTaskStatus.CREATED
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    dependencies: tuple[AgentRole, ...] = ()
    execution_step_id: str | None = None
    execution_mode: str | None = None


@dataclass(frozen=True)
class AgentResult:
    run_id: str
    task_id: str
    profile: AgentProfile
    content: str
    id: str = field(default_factory=lambda: new_id("agent_result"))
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunEvent:
    run_id: str
    type: RunEventType
    message: str
    id: str = field(default_factory=lambda: new_id("run_event"))
    agent_role: AgentRole | None = None
    agent_task_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)


@dataclass
class TeamRun:
    user_task: str
    id: str = field(default_factory=lambda: new_id("team_run"))
    status: RunStatus = RunStatus.CREATED
    tasks: list[AgentTask] = field(default_factory=list)
    results: list[AgentResult] = field(default_factory=list)
    events: list[RunEvent] = field(default_factory=list)
    messages: list[TeamMessage] = field(default_factory=list)
    dialogue_turns: list[TeamDialogueTurn] = field(default_factory=list)
    attachments: list[TeamInputAttachment] = field(default_factory=list)
    execution_mode: Any = "sequential"
    execution_plan: TeamExecutionPlan | None = None
    review_protocol_enabled: bool = True
    task_brief: TeamTaskBrief | None = None
    quality_criteria: list[TeamQualityCriterion] = field(default_factory=list)
    review_notes: list[TeamReviewNote] = field(default_factory=list)
    revision_requests: list[TeamRevisionRequest] = field(default_factory=list)
    review_decision: TeamReviewDecision | None = None
    final_package: TeamFinalPackage | None = None
    revision_loops_count: int = 0
    final_text: str | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True)
class TeamRunOutcome:
    run: TeamRun
    final_text: str
