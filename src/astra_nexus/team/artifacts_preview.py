from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from astra_nexus.config.settings import load_settings
from astra_nexus.team.attachments import TeamAttachmentProcessor
from astra_nexus.team.fake_provider import FakeTeamProvider
from astra_nexus.team.orchestrator import AsyncTeamOrchestrator
from astra_nexus.team.workspace import TeamRunWorkspace

DEFAULT_ARTIFACTS_TASK = "Собери итоговый вариант."


async def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    settings = load_settings()
    user_task = " ".join(args.task).strip() or DEFAULT_ARTIFACTS_TASK
    workspace_root = args.workspace_root or settings.team_runs_dir
    attachments = ()

    if args.files:
        attachments = TeamAttachmentProcessor(
            max_files=settings.team_attachments_max_files,
            max_bytes=settings.team_attachment_max_bytes,
            max_extracted_chars=settings.team_attachment_max_extracted_chars,
            max_prompt_chars=settings.team_attachment_max_prompt_chars,
            pdf_max_pages=settings.team_attachment_pdf_max_pages,
            docx_include_tables=settings.team_attachment_docx_include_tables,
        ).prepare_paths(tuple(args.files), source="artifacts_preview")

    orchestrator = AsyncTeamOrchestrator(
        provider=FakeTeamProvider(),
        max_revision_loops=settings.team_max_revision_loops,
    )
    outcome = await orchestrator.run(user_task, attachments=attachments)
    workspace_path = TeamRunWorkspace(root_path=workspace_root).save(outcome.run)
    artifacts_dir = workspace_path / "artifacts"

    print(f"run_id: {outcome.run.id}")
    print(f"status: {outcome.run.status.value}")
    print(f"workspace_path: {workspace_path}")
    print(f"artifacts_dir: {artifacts_dir}")
    print("artifacts:")
    for artifact in outcome.run.artifacts:
        print(
            f"- {artifact.relative_path} ({artifact.artifact_type.value}, {artifact.format.value})"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview AI Team output artifacts.")
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
