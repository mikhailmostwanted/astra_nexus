from __future__ import annotations

import asyncio

from astra_nexus.bootstrap import build_container
from astra_nexus.telegram.bot import run_bot


async def amain() -> None:
    container = build_container()
    await run_bot(
        settings=container.settings,
        orchestrator=container.orchestrator,
        task_service=container.task_service,
        agent_service=container.agent_service,
        message_service=container.message_service,
    )


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
