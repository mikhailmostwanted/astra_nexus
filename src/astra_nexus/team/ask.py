from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from astra_nexus.config.settings import load_settings
from astra_nexus.team.nodriver_provider import NoDriverTeamProvider
from astra_nexus.team.orchestrator import AsyncTeamOrchestrator
from astra_nexus.team.provider import TeamProvider, TeamProviderError
from astra_nexus.team.workspace import TeamRunWorkspace
from astra_nexus.utils.logging import configure_logging

DEFAULT_TASK = "Ответь кратко: что такое Astra Nexus?"


async def run(
    argv: list[str] | None = None,
    *,
    provider: TeamProvider | None = None,
) -> int:
    args = _parse_args(argv)
    user_task = " ".join(args.task).strip() or DEFAULT_TASK

    settings = load_settings()
    configure_logging(settings.log_level)
    provider = provider or NoDriverTeamProvider(settings=settings)
    orchestrator = AsyncTeamOrchestrator(
        provider=provider,
        workspace_path=args.workspace_root,
    )

    try:
        outcome = await orchestrator.run(user_task)
    except TeamProviderError as exc:
        run_path = _save_last_run(orchestrator, args.workspace_root)
        _print_failed_run(exc=exc, orchestrator=orchestrator, run_path=run_path)
        return 1
    finally:
        await _close_provider(provider)

    workspace_path = TeamRunWorkspace(root_path=args.workspace_root).save(outcome.run)
    print(f"status: {outcome.run.status.value}")
    print(f"run_id: {outcome.run.id}")
    print(f"workspace_path: {workspace_path}")
    print("final_result:")
    print(outcome.final_text)
    return 0


def main(
    argv: list[str] | None = None,
    *,
    provider: TeamProvider | None = None,
) -> int:
    return asyncio.run(run(argv, provider=provider))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AI Team pipeline with NoDriver provider.")
    parser.add_argument(
        "task",
        nargs="*",
        help="Текст задачи. Если не указан, используется дефолтная задача.",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path("data/team_runs"),
        help="Папка для team run workspaces.",
    )
    return parser.parse_args(argv)


def _save_last_run(orchestrator: AsyncTeamOrchestrator, workspace_root: Path) -> Path | None:
    if not orchestrator.runs:
        return None
    return TeamRunWorkspace(root_path=workspace_root).save(orchestrator.runs[-1])


def _print_failed_run(
    *,
    exc: TeamProviderError,
    orchestrator: AsyncTeamOrchestrator,
    run_path: Path | None,
) -> None:
    run = orchestrator.runs[-1] if orchestrator.runs else None
    print("status: failed")
    if run is not None:
        print(f"run_id: {run.id}")
    if run_path is not None:
        print(f"workspace_path: {run_path}")
    print(f"message: {exc}")


async def _close_provider(provider: TeamProvider) -> None:
    close = getattr(provider, "close", None)
    if close is None:
        return
    result = close()
    if asyncio.iscoroutine(result):
        await result


if __name__ == "__main__":
    raise SystemExit(main())
