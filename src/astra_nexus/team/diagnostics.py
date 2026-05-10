from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from astra_nexus.config.settings import Settings, load_settings
from astra_nexus.team.fake_provider import FakeTeamProvider
from astra_nexus.team.orchestrator import AsyncTeamOrchestrator
from astra_nexus.team.run_registry import TeamRunRegistry
from astra_nexus.team.workspace import TeamRunWorkspace


class DiagnosticStatus(StrEnum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"


@dataclass(frozen=True)
class DiagnosticCheck:
    name: str
    status: DiagnosticStatus
    message: str


@dataclass(frozen=True)
class MvpDiagnosticReport:
    status: DiagnosticStatus
    checks: list[DiagnosticCheck] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)


async def run_mvp_diagnostics(
    *,
    settings: Settings | None = None,
) -> MvpDiagnosticReport:
    settings = settings or load_settings()
    checks: list[DiagnosticCheck] = []
    next_actions: list[str] = []

    _add_check(checks, "Python/app import ok", DiagnosticStatus.OK, "astra_nexus импортируется.")
    _add_check(checks, "settings loaded", DiagnosticStatus.OK, "settings загружены.")

    provider = settings.team_telegram_provider.strip().lower()
    if provider in {"fake", "nodriver"}:
        _add_check(checks, "TEAM_TELEGRAM_PROVIDER", DiagnosticStatus.OK, provider)
    else:
        _add_check(
            checks,
            "TEAM_TELEGRAM_PROVIDER",
            DiagnosticStatus.ERROR,
            f"неизвестный provider: {settings.team_telegram_provider}",
        )
        next_actions.append(
            "Укажи TEAM_TELEGRAM_PROVIDER=fake или TEAM_TELEGRAM_PROVIDER=nodriver."
        )

    if settings.telegram_bot_token is None:
        _add_check(
            checks,
            "TELEGRAM_BOT_TOKEN",
            DiagnosticStatus.WARN,
            "не задан; polling bot не стартует, preview/diagnostics работают.",
        )
        next_actions.append("Для live Telegram запуска добавь TELEGRAM_BOT_TOKEN в .env.")
    else:
        _add_check(checks, "TELEGRAM_BOT_TOKEN", DiagnosticStatus.OK, "задан.")

    if settings.team_telegram_allowed_chat_ids.strip():
        _add_check(
            checks,
            "TEAM_TELEGRAM_ALLOWED_CHAT_IDS",
            DiagnosticStatus.OK,
            settings.team_telegram_allowed_chat_ids,
        )
    else:
        _add_check(
            checks,
            "TEAM_TELEGRAM_ALLOWED_CHAT_IDS",
            DiagnosticStatus.WARN,
            "не задан; в local/dev/test bridge разрешает все чаты.",
        )
        next_actions.append("Перед live тестом лучше указать TEAM_TELEGRAM_ALLOWED_CHAT_IDS.")

    if settings.team_telegram_log_chat_id is None:
        _add_check(
            checks,
            "TEAM_TELEGRAM_LOG_CHAT_ID",
            DiagnosticStatus.WARN,
            "не задан; технический log chat отключён.",
        )
        next_actions.append("Для live диагностики укажи TEAM_TELEGRAM_LOG_CHAT_ID.")
    else:
        _add_check(
            checks,
            "TEAM_TELEGRAM_LOG_CHAT_ID",
            DiagnosticStatus.OK,
            str(settings.team_telegram_log_chat_id),
        )

    _ensure_directory_check(checks, "TEAM_RUNS_DIR", settings.team_runs_dir)
    _ensure_directory_check(
        checks,
        "TEAM_TELEGRAM_DOWNLOADS_DIR",
        settings.team_telegram_downloads_dir,
    )

    fake_workspace_path = await _run_fake_provider_check(checks, settings=settings)
    _check_artifacts_dir(checks, fake_workspace_path)
    _check_registry(checks, settings.team_runs_dir)

    if provider == "nodriver":
        _add_check(
            checks,
            "nodriver live readiness",
            DiagnosticStatus.WARN,
            "NoDriver не запускается в mvp-check автоматически.",
        )
        next_actions.append(
            "Перед TEAM_TELEGRAM_PROVIDER=nodriver проверь astra-nexus-nodriver-smoke."
        )
        next_actions.append(
            'Затем проверь astra-nexus-nodriver-ask "Ответь ровно так: Astra Nexus online."'
        )
    else:
        _add_check(
            checks,
            "nodriver live readiness",
            DiagnosticStatus.OK,
            "provider=fake; NoDriver для этого запуска не нужен.",
        )

    if provider == "fake" and settings.telegram_bot_token is not None:
        next_actions.append("Можно запускать astra-nexus-team-telegram-bot для local live test.")
    next_actions.append("Проверь /help, /health, /status, /runs и /stopall в Telegram.")

    return MvpDiagnosticReport(
        status=_overall_status(checks),
        checks=checks,
        next_actions=_deduplicate(next_actions),
    )


def main(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
) -> int:
    args = _parse_args(argv)
    report = asyncio.run(run_mvp_diagnostics(settings=settings))
    print(render_report(report, verbose=args.verbose))
    return 1 if report.status == DiagnosticStatus.ERROR else 0


def render_report(report: MvpDiagnosticReport, *, verbose: bool = False) -> str:
    lines = [
        "Astra Nexus AI Team MVP check",
        f"status: {report.status.value}",
        "",
        "checks:",
    ]
    for check in report.checks:
        lines.append(f"- [{check.status.value}] {check.name}: {check.message}")
    lines.extend(["", "next actions:"])
    if report.next_actions:
        lines.extend(f"- {action}" for action in report.next_actions)
    else:
        lines.append("- Действий не требуется.")
    if verbose:
        lines.extend(["", "notes:", "- mvp-check не вызывает реальный Telegram API."])
    return "\n".join(lines)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check local AI Team Telegram MVP readiness.")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args(argv)


def _add_check(
    checks: list[DiagnosticCheck],
    name: str,
    status: DiagnosticStatus,
    message: str,
) -> None:
    checks.append(DiagnosticCheck(name=name, status=status, message=message))


def _ensure_directory_check(
    checks: list[DiagnosticCheck],
    name: str,
    path: Path,
) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        _add_check(checks, name, DiagnosticStatus.ERROR, f"нельзя создать {path}: {exc}")
        return
    _add_check(checks, name, DiagnosticStatus.OK, f"{path}")


async def _run_fake_provider_check(
    checks: list[DiagnosticCheck],
    *,
    settings: Settings,
) -> Path | None:
    try:
        outcome = await AsyncTeamOrchestrator(provider=FakeTeamProvider()).run(
            "mvp-check: короткая проверка fake provider"
        )
        workspace_path = TeamRunWorkspace(root_path=settings.team_runs_dir).save(outcome.run)
    except Exception as exc:
        _add_check(checks, "fake provider short run", DiagnosticStatus.ERROR, str(exc))
        return None
    _add_check(
        checks,
        "fake provider short run",
        DiagnosticStatus.OK,
        f"completed run_id={outcome.run.id}",
    )
    return workspace_path


def _check_artifacts_dir(checks: list[DiagnosticCheck], workspace_path: Path | None) -> None:
    if workspace_path is None:
        _add_check(
            checks,
            "artifacts dir",
            DiagnosticStatus.ERROR,
            "fake run не создал workspace.",
        )
        return
    artifacts_dir = workspace_path / "artifacts"
    if artifacts_dir.exists() and (artifacts_dir / "final_answer.md").exists():
        _add_check(checks, "artifacts dir", DiagnosticStatus.OK, str(artifacts_dir))
        return
    _add_check(
        checks,
        "artifacts dir",
        DiagnosticStatus.ERROR,
        f"нет ожидаемых artifacts в {artifacts_dir}",
    )


def _check_registry(checks: list[DiagnosticCheck], runs_dir: Path) -> None:
    try:
        entries = TeamRunRegistry(runs_dir).latest_runs(limit=5)
    except Exception as exc:
        _add_check(checks, "run registry", DiagnosticStatus.ERROR, str(exc))
        return
    _add_check(checks, "run registry", DiagnosticStatus.OK, f"прочитано runs: {len(entries)}")


def _overall_status(checks: list[DiagnosticCheck]) -> DiagnosticStatus:
    statuses = {check.status for check in checks}
    if DiagnosticStatus.ERROR in statuses:
        return DiagnosticStatus.ERROR
    if DiagnosticStatus.WARN in statuses:
        return DiagnosticStatus.WARN
    return DiagnosticStatus.OK


def _deduplicate(items: list[str]) -> list[str]:
    unique: list[str] = []
    for item in items:
        if item not in unique:
            unique.append(item)
    return unique


if __name__ == "__main__":
    raise SystemExit(main())
