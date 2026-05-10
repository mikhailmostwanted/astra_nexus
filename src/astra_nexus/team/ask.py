from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from astra_nexus.config.settings import load_settings
from astra_nexus.team.nodriver_provider import NoDriverTeamProvider
from astra_nexus.team.orchestrator import AsyncTeamOrchestrator, TeamRetryPolicy
from astra_nexus.team.prompting import TeamPromptBuilder
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
    workspace_root = args.workspace_root or settings.team_runs_dir
    provider = provider or NoDriverTeamProvider(settings=settings)
    orchestrator = AsyncTeamOrchestrator(
        provider=provider,
        prompt_builder=TeamPromptBuilder(
            previous_results_max_chars=args.previous_results_max_chars
            or settings.team_previous_results_max_chars
        ),
        workspace_path=workspace_root,
        retry_policy=TeamRetryPolicy(
            max_retries=(
                args.max_retries
                if args.max_retries is not None
                else settings.team_agent_max_retries
            ),
            retry_delay_seconds=(
                args.retry_delay_seconds
                if args.retry_delay_seconds is not None
                else settings.team_agent_retry_delay_seconds
            ),
            response_timeout_seconds=(
                args.response_timeout_seconds
                if args.response_timeout_seconds is not None
                else settings.team_agent_response_timeout_seconds
            ),
        ),
        execution_mode=settings.team_execution_mode,
        max_parallel_agents=settings.team_max_parallel_agents,
        parallel_agent_timeout_seconds=settings.team_parallel_agent_timeout_seconds,
    )

    try:
        outcome = await orchestrator.run(user_task)
    except TeamProviderError as exc:
        run_path = _save_last_run(orchestrator, workspace_root)
        _print_failed_run(exc=exc, orchestrator=orchestrator, run_path=run_path)
        return 1
    finally:
        await _close_provider(provider)

    workspace_path = TeamRunWorkspace(root_path=workspace_root).save(outcome.run)
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
        default=None,
        help="Папка для team run workspaces.",
    )
    parser.add_argument("--max-retries", type=int, default=None)
    parser.add_argument("--retry-delay-seconds", type=float, default=None)
    parser.add_argument("--response-timeout-seconds", type=float, default=None)
    parser.add_argument("--previous-results-max-chars", type=int, default=None)
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
    if run is not None:
        print(f"Можно продолжить: astra-nexus-team-resume {run.id}")


async def _close_provider(provider: TeamProvider) -> None:
    close = getattr(provider, "close", None)
    if close is None:
        return
    result = close()
    if asyncio.iscoroutine(result):
        await result


if __name__ == "__main__":
    raise SystemExit(main())
