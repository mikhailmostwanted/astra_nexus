from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from astra_nexus.config.settings import load_settings
from astra_nexus.team.fake_provider import FakeTeamProvider
from astra_nexus.team.runtime import TeamConversationController
from astra_nexus.team.workspace import TeamRunWorkspace


async def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    message = " ".join(args.message).strip() or "сделай краткий план и пришли docx"
    settings = load_settings()
    workspace_root = args.workspace_root or settings.team_runs_dir
    controller = TeamConversationController(
        provider=FakeTeamProvider(),
        workspace=TeamRunWorkspace(root_path=workspace_root),
    )
    response = await controller.handle(message)
    print(f"status: {response.status.value}")
    print(f"intent: {response.decision.intent.value}")
    print(f"run_id: {response.run_id or ''}")
    print(f"workspace: {response.workspace_path or ''}")
    if response.workspace_path is None:
        return 0
    run_payload = _read_json(response.workspace_path / "run.json")
    print(f"requested_files_dir: {response.workspace_path / 'requested_files'}")
    print(f"request_json: {response.workspace_path / 'requested_file_request.json'}")
    print(
        f"download_result_json: {response.workspace_path / 'requested_file_download_result.json'}"
    )
    for artifact in run_payload.get("artifacts", []):
        if isinstance(artifact, dict) and artifact.get("artifact_type") == "requested_output":
            print(f"fallback_requested_output: {artifact.get('path')}")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview requested-file team workspace output.")
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=None,
        help="Workspace root for the preview run.",
    )
    parser.add_argument("message", nargs=argparse.REMAINDER)
    return parser.parse_args(argv)


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
