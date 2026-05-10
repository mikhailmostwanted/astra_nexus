from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from astra_nexus.config.settings import load_settings
from astra_nexus.team.execution_plan import TeamExecutionMode
from astra_nexus.team.fake_provider import FakeTeamProvider
from astra_nexus.team.models import RunEventType
from astra_nexus.team.orchestrator import AsyncTeamOrchestrator
from astra_nexus.team.workspace import TeamRunWorkspace

DEFAULT_PARALLEL_TASK = "Проверь идею AI-команды."


async def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = load_settings()
    user_task = " ".join(args.task).strip() or DEFAULT_PARALLEL_TASK
    workspace_root = args.workspace_root or settings.team_runs_dir
    orchestrator = AsyncTeamOrchestrator(
        provider=FakeTeamProvider(),
        execution_mode=TeamExecutionMode.PARALLEL,
        max_parallel_agents=args.max_parallel_agents,
        parallel_agent_timeout_seconds=args.parallel_agent_timeout_seconds,
    )

    outcome = await orchestrator.run(user_task)
    workspace_path = TeamRunWorkspace(root_path=workspace_root).save(outcome.run)

    print(f"status: {outcome.run.status.value}")
    print(f"run_id: {outcome.run.id}")
    print(f"execution_mode: {outcome.run.execution_mode.value}")
    print(f"workspace_path: {workspace_path}")
    print("events:")
    for event in outcome.run.events:
        if event.type in {RunEventType.AGENT_STARTED, RunEventType.AGENT_FINISHED}:
            print(f"{event.type.value}: {event.agent_role.value if event.agent_role else ''}")
    print("final_result:")
    print(outcome.final_text)
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview AI Team parallel execution foundation with fake provider."
    )
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
    parser.add_argument("--max-parallel-agents", type=int, default=2)
    parser.add_argument("--parallel-agent-timeout-seconds", type=float, default=240.0)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
