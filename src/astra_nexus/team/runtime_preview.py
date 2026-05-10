from __future__ import annotations

import argparse
import asyncio

from astra_nexus.team.fake_provider import FakeTeamProvider
from astra_nexus.team.runtime import TeamConversationController
from astra_nexus.team.workspace import TeamRunWorkspace

DEFAULT_MESSAGE = "брат че думаешь"


async def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    message = " ".join(args.message).strip() or DEFAULT_MESSAGE
    controller = TeamConversationController(
        provider=FakeTeamProvider(),
        workspace=TeamRunWorkspace(root_path=args.workspace_root),
    )
    response = await controller.handle(message)

    print(f"intent: {response.decision.intent.value}")
    print(f"status: {response.status.value}")
    print(f"reason: {response.decision.reason}")
    print(f"user_visible_reply: {response.user_visible_reply}")
    if response.run_id:
        print(f"run_id: {response.run_id}")
    if response.final_text:
        print("final_text:")
        print(response.final_text)
    if response.workspace_path:
        print(f"workspace_path: {response.workspace_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview AI Team runtime controller flow.")
    parser.add_argument(
        "message",
        nargs="*",
        help="Входящее сообщение пользователя.",
    )
    parser.add_argument(
        "--workspace-root",
        default="data/team_runs",
        help="Папка для workspace run, если runtime запускает команду.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
