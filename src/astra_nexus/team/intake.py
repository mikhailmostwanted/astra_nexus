from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from astra_nexus.team.attachments import TeamInputAttachment
from astra_nexus.team.fake_provider import FakeTeamProvider
from astra_nexus.team.models import TeamRun
from astra_nexus.team.orchestrator import AsyncTeamOrchestrator
from astra_nexus.team.provider import TeamProvider
from astra_nexus.team.workspace import TeamRunWorkspace


class TeamInputIntent(StrEnum):
    CASUAL_CHAT = "casual_chat"
    NEW_TASK = "new_task"
    TASK_FOLLOWUP = "task_followup"
    REVISE_PREVIOUS_RESULT = "revise_previous_result"
    FILE_TASK = "file_task"
    STATUS_REQUEST = "status_request"
    RESUME_RUN = "resume_run"
    STOP_ALL = "stop_all"
    EMPTY_INPUT = "empty_input"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TeamInput:
    text: str = ""
    attachments: tuple[TeamInputAttachment, ...] = ()
    attachments_count: int = 0
    active_run_id: str | None = None
    last_run_id: str | None = None
    failed_run_id: str | None = None
    has_active_run: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.attachments and self.attachments_count == 0:
            object.__setattr__(self, "attachments_count", len(self.attachments))

    @property
    def normalized_text(self) -> str:
        return " ".join(self.text.strip().lower().split())


@dataclass(frozen=True)
class TeamIntakeDecision:
    intent: TeamInputIntent
    confidence: float
    reason: str
    should_start_run: bool = False
    should_resume_run: bool = False
    should_stop_runs: bool = False
    user_visible_reply: str = ""
    target_run_id: str | None = None


@dataclass(frozen=True)
class TeamConversationResult:
    team_input: TeamInput
    decision: TeamIntakeDecision
    outcome: Any | None = None
    workspace_path: Path | None = None


class TeamIntakeRouter:
    stop_phrases = ("/stopall", "стоп все", "стоп всё", "останови всё", "остановить всех")
    status_phrases = (
        "/status",
        "статус",
        "что сейчас",
        "как дела с задачей",
        "что происходит",
    )
    resume_phrases = ("продолжи", "resume", "доделай прошлое", "продолжи прошлое")
    task_verbs = (
        "сделай",
        "проверь",
        "напиши",
        "составь",
        "проанализируй",
        "улучши",
        "перепиши",
        "подготовь",
        "разбери",
    )
    followup_phrases = (
        "добавь",
        "измени",
        "учти",
        "сделай мягче",
        "короче",
        "жестче",
        "жёстче",
    )
    revision_phrases = (
        "перепиши",
        "сделай лучше",
        "сократи",
        "расширь",
        "мягче",
        "формальнее",
    )

    def route(self, team_input: TeamInput | str) -> TeamIntakeDecision:
        if isinstance(team_input, str):
            team_input = TeamInput(text=team_input)

        text = team_input.normalized_text
        if not text and team_input.attachments_count <= 0:
            return self._decision(
                TeamInputIntent.EMPTY_INPUT,
                1.0,
                "empty text without attachments",
                "Не вижу задачи. Напиши, что нужно сделать.",
            )
        if self._contains_any(text, self.stop_phrases):
            return self._decision(
                TeamInputIntent.STOP_ALL,
                0.98,
                "explicit stop command",
                "Останавливаю активные процессы команды.",
                should_stop_runs=True,
            )
        if self._contains_any(text, self.status_phrases):
            return self._decision(
                TeamInputIntent.STATUS_REQUEST,
                0.92,
                "status phrase detected",
                "Сейчас проверю статус активных задач.",
            )
        if team_input.failed_run_id and self._contains_any(text, self.resume_phrases):
            return self._decision(
                TeamInputIntent.RESUME_RUN,
                0.94,
                "resume phrase with failed run id",
                f"Продолжаю сохранённый run {team_input.failed_run_id}.",
                should_resume_run=True,
                target_run_id=team_input.failed_run_id,
            )
        if team_input.attachments_count > 0:
            if text:
                return self._decision(
                    TeamInputIntent.FILE_TASK,
                    0.88,
                    "attachments with task text",
                    "Вижу файл и задачу. Запускаю команду.",
                    should_start_run=True,
                )
            return self._decision(
                TeamInputIntent.FILE_TASK,
                0.82,
                "attachments without task text",
                "Вижу файл. Запускаю команду.",
                should_start_run=True,
            )
        if team_input.active_run_id and self._contains_any(text, self.followup_phrases):
            return self._decision(
                TeamInputIntent.TASK_FOLLOWUP,
                0.86,
                "follow-up phrase with active run id",
                "Принял уточнение к активной задаче.",
                target_run_id=team_input.active_run_id,
            )
        if team_input.has_active_run and self._contains_any(text, self.followup_phrases):
            return self._decision(
                TeamInputIntent.TASK_FOLLOWUP,
                0.82,
                "follow-up phrase with active run flag",
                "Принял уточнение к активной задаче.",
            )
        if team_input.last_run_id and self._contains_any(text, self.revision_phrases):
            return self._decision(
                TeamInputIntent.REVISE_PREVIOUS_RESULT,
                0.84,
                "revision phrase with last run id",
                "Понял, это правка предыдущего результата.",
                target_run_id=team_input.last_run_id,
            )
        if self._looks_like_new_task(text):
            return self._decision(
                TeamInputIntent.NEW_TASK,
                0.88,
                "task verb or long task-like text detected",
                "Понял задачу. Запускаю команду.",
                should_start_run=True,
            )
        if text:
            return self._decision(
                TeamInputIntent.CASUAL_CHAT,
                0.72,
                "short message without task markers",
                "Понял, это обычный диалог, команду не запускаю.",
            )
        return self._decision(
            TeamInputIntent.UNKNOWN,
            0.3,
            "input did not match known routing rules",
            "Не понял, это задача или обычное сообщение. Напиши чуть конкретнее.",
        )

    def _decision(
        self,
        intent: TeamInputIntent,
        confidence: float,
        reason: str,
        user_visible_reply: str,
        *,
        should_start_run: bool = False,
        should_resume_run: bool = False,
        should_stop_runs: bool = False,
        target_run_id: str | None = None,
    ) -> TeamIntakeDecision:
        return TeamIntakeDecision(
            intent=intent,
            confidence=confidence,
            reason=reason,
            should_start_run=should_start_run,
            should_resume_run=should_resume_run,
            should_stop_runs=should_stop_runs,
            user_visible_reply=user_visible_reply,
            target_run_id=target_run_id,
        )

    def _looks_like_new_task(self, text: str) -> bool:
        if self._contains_any(text, self.task_verbs):
            return True
        words = text.split()
        return len(words) >= 18 or len(text) >= 140

    def _contains_any(self, text: str, phrases: tuple[str, ...]) -> bool:
        return any(phrase in text for phrase in phrases)


OrchestratorFactory = Callable[[TeamProvider], AsyncTeamOrchestrator]


class TeamConversationController:
    def __init__(
        self,
        *,
        router: TeamIntakeRouter | None = None,
        provider: TeamProvider | None = None,
        workspace: TeamRunWorkspace | None = None,
        orchestrator_factory: OrchestratorFactory | None = None,
    ) -> None:
        self.router = router or TeamIntakeRouter()
        self.provider = provider or FakeTeamProvider()
        self.workspace = workspace
        self.orchestrator_factory = orchestrator_factory
        self.runs: list[TeamRun] = []

    async def handle(self, team_input: TeamInput | str) -> TeamConversationResult:
        if isinstance(team_input, str):
            team_input = TeamInput(text=team_input)
        decision = self.router.route(team_input)

        if decision.should_start_run:
            orchestrator = self._orchestrator()
            outcome = await orchestrator.run(
                team_input.text.strip(),
                attachments=team_input.attachments,
            )
            self.runs.append(outcome.run)
            workspace_path = self.workspace.save(outcome.run) if self.workspace else None
            return TeamConversationResult(
                team_input=team_input,
                decision=decision,
                outcome=outcome,
                workspace_path=workspace_path,
            )

        if decision.should_resume_run and self.workspace is not None and decision.target_run_id:
            run = self.workspace.load(decision.target_run_id)
            orchestrator = self._orchestrator()
            outcome = await orchestrator.resume(run)
            self.runs.append(outcome.run)
            workspace_path = self.workspace.save(outcome.run)
            return TeamConversationResult(
                team_input=team_input,
                decision=decision,
                outcome=outcome,
                workspace_path=workspace_path,
            )

        return TeamConversationResult(team_input=team_input, decision=decision)

    def _orchestrator(self) -> AsyncTeamOrchestrator:
        if self.orchestrator_factory is not None:
            return self.orchestrator_factory(self.provider)
        return AsyncTeamOrchestrator(provider=self.provider)
