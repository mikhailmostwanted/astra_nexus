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
    SIMPLE_ANSWER = "simple_answer"
    TEAM_STANDARD = "team_standard"
    TEAM_DEEP = "team_deep"
    NEW_TASK = "new_task"
    TASK_FOLLOWUP = "task_followup"
    REVISE_PREVIOUS_RESULT = "revise_previous_result"
    FILE_TASK = "file_task"
    FILE_GENERATION = "file_generation"
    DEBUG_MODE = "debug_mode"
    STATUS_REQUEST = "status_request"
    RUNS_REQUEST = "runs_request"
    HEALTH_REQUEST = "health_request"
    HELP_REQUEST = "help_request"
    RESUME_RUN = "resume_run"
    STOP_ALL = "stop_all"
    EMPTY_INPUT = "empty_input"
    UNKNOWN = "unknown"


REQUESTED_OUTPUT_FORMATS = {
    "docx",
    "jpg",
    "jpeg",
    "md",
    "pdf",
    "png",
    "pptx",
    "txt",
    "webp",
    "xlsx",
    "zip",
    "unknown",
}


@dataclass(frozen=True)
class TeamInput:
    text: str = ""
    attachments: tuple[TeamInputAttachment, ...] = ()
    attachments_count: int = 0
    active_run_id: str | None = None
    last_run_id: str | None = None
    failed_run_id: str | None = None
    has_active_run: bool = False
    output_requested_as_file: bool = False
    requested_output_format: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.attachments and self.attachments_count == 0:
            object.__setattr__(self, "attachments_count", len(self.attachments))
        requested, output_format = detect_requested_output_artifact(self.text)
        effective_requested = self.output_requested_as_file or requested
        if requested and not self.output_requested_as_file:
            object.__setattr__(self, "output_requested_as_file", True)
        normalized_format = _normalize_requested_output_format(
            self.requested_output_format if self.output_requested_as_file else output_format
        )
        if effective_requested and normalized_format == "unknown" and output_format != "unknown":
            normalized_format = output_format
        object.__setattr__(self, "requested_output_format", normalized_format)

    @property
    def normalized_text(self) -> str:
        return " ".join(self.text.strip().lower().split())


def detect_requested_output_artifact(text: str) -> tuple[bool, str]:
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return False, "unknown"
    output_format = "unknown"
    if any(token in normalized for token in ("docx", ".docx", "ворд", "word")):
        output_format = "docx"
    elif any(token in normalized for token in ("xlsx", ".xlsx", "excel", "эксель")):
        output_format = "xlsx"
    elif any(
        token in normalized
        for token in ("pptx", ".pptx", "powerpoint", "presentation", "презентац")
    ):
        output_format = "pptx"
    elif any(token in normalized for token in ("pdf", ".pdf", "пдф")):
        output_format = "pdf"
    elif any(token in normalized for token in ("zip", ".zip", "архив")):
        output_format = "zip"
    elif any(token in normalized for token in ("png", ".png")):
        output_format = "png"
    elif any(token in normalized for token in ("jpg", ".jpg", "jpeg", ".jpeg")):
        output_format = "jpg"
    elif any(token in normalized for token in ("webp", ".webp")):
        output_format = "webp"
    elif any(token in normalized for token in ("txt", ".txt", "текстовым файлом")):
        output_format = "txt"
    elif any(token in normalized for token in ("md", ".md", "markdown", "маркдаун")):
        output_format = "md"

    file_markers = (
        "пришли файлом",
        "отправь файлом",
        "выдай файлом",
        "сделай файл",
        "собери файл",
        "собери документ",
        "сделай документ",
        "документом",
        "в виде файла",
        "как файл",
    )
    format_markers = (
        "сделай docx",
        "сделай xlsx",
        "сделай pptx",
        "сделай pdf",
        "сделай txt",
        "сделай md",
        "сделай zip",
        "собери docx",
        "собери xlsx",
        "собери pptx",
        "собери pdf",
        "собери txt",
        "собери md",
        "собери zip",
        "пришли docx",
        "пришли xlsx",
        "пришли pptx",
        "пришли pdf",
        "пришли txt",
        "пришли md",
        "пришли zip",
    )
    requested = any(marker in normalized for marker in file_markers + format_markers)
    if not requested and output_format in {"docx", "pdf", "xlsx", "pptx", "zip"}:
        requested = any(verb in normalized for verb in ("сделай", "собери", "подготовь"))
    return requested, output_format


def _normalize_requested_output_format(value: str) -> str:
    normalized = str(value or "unknown").strip().lower().lstrip(".")
    return normalized if normalized in REQUESTED_OUTPUT_FORMATS else "unknown"


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
    runs_phrases = ("/runs",)
    health_phrases = ("/health",)
    help_phrases = ("/help", "help")
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

        if "/debug" in text:
            return self._decision(
                TeamInputIntent.DEBUG_MODE,
                0.95,
                "explicit debug mode requested",
                "Включаю режим глубокой диагностики.",
                should_start_run=True,
            )
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
        if self._contains_any(text, self.runs_phrases):
            return self._decision(
                TeamInputIntent.RUNS_REQUEST,
                0.98,
                "runs command detected",
                "Сейчас покажу последние запуски команды.",
            )
        if self._contains_any(text, self.health_phrases):
            return self._decision(
                TeamInputIntent.HEALTH_REQUEST,
                0.98,
                "health command detected",
                "Сейчас проверю состояние Telegram runtime.",
            )
        if self._contains_any(text, self.help_phrases):
            return self._decision(
                TeamInputIntent.HELP_REQUEST,
                0.98,
                "help command detected",
                "Показываю доступные команды AI-команды.",
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
            if text and self._looks_like_new_task(text):
                return self._decision(
                    TeamInputIntent.FILE_TASK,
                    0.95,
                    "attachments with task text",
                    "Вижу файл и задачу. Запускаю обработку.",
                    should_start_run=True,
                )
            return self._decision(
                TeamInputIntent.FILE_TASK,
                0.82,
                "attachments without clear task text",
                (
                    "Босс, файл вижу, но задачи к нему нет. Напиши, что с ним сделать: "
                    "проверить, переписать, сократить, сравнить или собрать итоговый вариант."
                ),
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
        if team_input.output_requested_as_file:
            return self._decision(
                TeamInputIntent.FILE_GENERATION,
                0.90,
                "file generation requested",
                f"Принял задачу на генерацию файла ({team_input.requested_output_format}).",
                should_start_run=True,
            )

        if self._looks_like_new_task(text):
            intent = TeamInputIntent.TEAM_STANDARD
            if any(word in text for word in ("подробно", "детально", "глубоко", "deep")):
                intent = TeamInputIntent.TEAM_DEEP

            return self._decision(
                intent,
                0.88,
                "task verb or long task-like text detected",
                "Понял задачу. Запускаю команду.",
                should_start_run=True,
            )
        if text:
            if len(text.split()) < 5:
                return self._decision(
                    TeamInputIntent.CASUAL_CHAT,
                    0.72,
                    "short message without task markers",
                    "Босс, я на связи. Можем спокойно обсудить или сразу превратить мысль в задачу.",
                )
            else:
                return self._decision(
                    TeamInputIntent.SIMPLE_ANSWER,
                    0.75,
                    "medium length message without task markers",
                    "Принял. Сейчас отвечу.",
                    should_start_run=True,
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
                intent=decision.intent.value,
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
