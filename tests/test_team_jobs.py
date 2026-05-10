from __future__ import annotations

import asyncio

import pytest

from astra_nexus.team.intake import TeamInputIntent, TeamIntakeDecision
from astra_nexus.team.jobs import TeamJobAlreadyActiveError, TeamJobManager, TeamJobStatus
from astra_nexus.team.runtime import TeamRuntimeResponse, TeamRuntimeStatus


def _decision() -> TeamIntakeDecision:
    return TeamIntakeDecision(
        intent=TeamInputIntent.NEW_TASK,
        confidence=1.0,
        reason="test",
        should_start_run=True,
        user_visible_reply="Запускаю команду.",
    )


def _response(
    *,
    status: TeamRuntimeStatus = TeamRuntimeStatus.COMPLETED,
    run_id: str | None = "team_run_test",
) -> TeamRuntimeResponse:
    return TeamRuntimeResponse(
        user_visible_reply="final text",
        decision=_decision(),
        status=status,
        run_id=run_id,
        final_text="final text" if status == TeamRuntimeStatus.COMPLETED else None,
    )


def test_team_job_manager_starts_background_task() -> None:
    async def scenario() -> None:
        manager = TeamJobManager()
        release = asyncio.Event()
        started = asyncio.Event()

        async def runner() -> TeamRuntimeResponse:
            started.set()
            await release.wait()
            return _response()

        handle = manager.start(session_id="chat:100", user_task="сделай план", runner=runner)
        await asyncio.wait_for(started.wait(), timeout=1)

        snapshot = manager.snapshot("chat:100")
        assert snapshot is not None
        assert snapshot.status == TeamJobStatus.RUNNING
        assert handle.done() is False

        release.set()
        completed = await asyncio.wait_for(handle.wait(), timeout=1)

        assert completed.status == TeamJobStatus.COMPLETED
        assert completed.run_id == "team_run_test"
        assert manager.snapshot("chat:100").status == TeamJobStatus.COMPLETED

    asyncio.run(scenario())


def test_team_job_manager_rejects_second_active_job() -> None:
    async def scenario() -> None:
        manager = TeamJobManager()
        release = asyncio.Event()

        async def runner() -> TeamRuntimeResponse:
            await release.wait()
            return _response()

        handle = manager.start(session_id="chat:100", user_task="первая", runner=runner)
        with pytest.raises(TeamJobAlreadyActiveError):
            manager.start(session_id="chat:100", user_task="вторая", runner=runner)

        release.set()
        await handle.wait()

    asyncio.run(scenario())


def test_team_job_manager_cancels_active_job() -> None:
    async def scenario() -> None:
        manager = TeamJobManager()
        started = asyncio.Event()

        async def runner() -> TeamRuntimeResponse:
            started.set()
            await asyncio.sleep(30)
            return _response()

        manager.start(session_id="chat:100", user_task="долгая задача", runner=runner)
        await asyncio.wait_for(started.wait(), timeout=1)

        snapshot = await manager.cancel_active("chat:100", reason="stopall")

        assert snapshot is not None
        assert snapshot.status == TeamJobStatus.CANCELLED
        assert snapshot.error_message == "stopall"
        assert manager.snapshot("chat:100").status == TeamJobStatus.CANCELLED

    asyncio.run(scenario())


def test_team_job_manager_saves_failed_job_from_response() -> None:
    async def scenario() -> None:
        manager = TeamJobManager()

        async def runner() -> TeamRuntimeResponse:
            return _response(status=TeamRuntimeStatus.FAILED, run_id="team_run_failed")

        handle = manager.start(session_id="chat:100", user_task="сломайся", runner=runner)
        snapshot = await handle.wait()

        assert snapshot.status == TeamJobStatus.FAILED
        assert snapshot.run_id == "team_run_failed"
        assert manager.last_failed("chat:100").job_id == snapshot.job_id

    asyncio.run(scenario())


def test_team_job_manager_saves_failed_job_from_exception() -> None:
    async def scenario() -> None:
        manager = TeamJobManager()

        async def runner() -> TeamRuntimeResponse:
            raise RuntimeError("provider exploded")

        handle = manager.start(session_id="chat:100", user_task="сломайся", runner=runner)
        snapshot = await handle.wait()

        assert snapshot.status == TeamJobStatus.FAILED
        assert snapshot.error_message == "provider exploded"
        assert manager.last_failed("chat:100").job_id == snapshot.job_id

    asyncio.run(scenario())
