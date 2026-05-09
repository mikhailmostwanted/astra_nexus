from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from astra_nexus.team.fake_provider import FakeTeamProvider
from astra_nexus.team.orchestrator import AsyncTeamOrchestrator
from astra_nexus.team.workspace import TeamRunWorkspace

DEFAULT_TASK = "Составь краткий план улучшения Astra Nexus."


async def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    user_task = " ".join(args.task).strip() or DEFAULT_TASK

    orchestrator = AsyncTeamOrchestrator(provider=FakeTeamProvider())
    outcome = await orchestrator.run(user_task)
    workspace_path = TeamRunWorkspace(root_path=args.workspace_root).save(outcome.run)

    print(f"status: {outcome.run.status.value}")
    print(f"run_id: {outcome.run.id}")
    print("final_result:")
    print(outcome.final_text)
    print(f"workspace_path: {workspace_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AI Team smoke flow with fake provider.")
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


if __name__ == "__main__":
    raise SystemExit(main())
