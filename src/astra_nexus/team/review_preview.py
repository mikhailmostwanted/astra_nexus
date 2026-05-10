from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from astra_nexus.config.settings import load_settings
from astra_nexus.team.attachments import TeamAttachmentProcessor
from astra_nexus.team.fake_provider import FakeTeamProvider
from astra_nexus.team.orchestrator import AsyncTeamOrchestrator
from astra_nexus.team.workspace import TeamRunWorkspace

DEFAULT_REVIEW_TASK = "Проверь идею AI-команды."


async def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = load_settings()
    user_task = " ".join(args.task).strip() or DEFAULT_REVIEW_TASK
    workspace_root = args.workspace_root or settings.team_runs_dir
    attachments = ()
    if args.files:
        attachments = TeamAttachmentProcessor(
            max_files=settings.team_attachments_max_files,
            max_bytes=settings.team_attachment_max_bytes,
            text_max_chars=settings.team_attachment_text_max_chars,
        ).prepare_paths(tuple(args.files), source="review_preview")

    orchestrator = AsyncTeamOrchestrator(
        provider=FakeTeamProvider(),
        max_revision_loops=settings.team_max_revision_loops,
    )
    outcome = await orchestrator.run(user_task, attachments=attachments)
    workspace_path = TeamRunWorkspace(root_path=workspace_root).save(outcome.run)

    print("final result:")
    print(outcome.final_text)
    print("")
    print("brief:")
    if outcome.run.task_brief is None:
        print("not created")
    else:
        print(f"goal: {outcome.run.task_brief.normalized_goal}")
        print(f"expected_output: {outcome.run.task_brief.expected_output}")
    print("")
    print("review decision:")
    if outcome.run.review_decision is None:
        print("not created")
    else:
        print(f"approved: {outcome.run.review_decision.approved}")
        print(f"needs_revision: {outcome.run.review_decision.needs_revision}")
        print(f"summary: {outcome.run.review_decision.summary}")
    print(f"revision loops count: {outcome.run.revision_loops_count}")
    print(f"workspace path: {workspace_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview AI Team review protocol.")
    parser.add_argument("--file", action="append", dest="files", type=Path)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Папка для team run workspaces.",
    )
    parser.add_argument(
        "task",
        nargs="*",
        help="Текст задачи. Если не указан, используется дефолтная задача.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
