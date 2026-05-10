from __future__ import annotations

import asyncio
import inspect
import json
import sys
from datetime import UTC, datetime, timedelta

from astra_nexus.team import run_registry as run_registry_module
from astra_nexus.team.fake_provider import FakeTeamProvider
from astra_nexus.team.jobs import TeamJobStatus
from astra_nexus.team.run_registry import TeamRunRegistry
from astra_nexus.team.runtime import TeamRuntimeStatus
from astra_nexus.team.telegram_bridge import (
    RecordingTelegramBot,
    TelegramTeamBridge,
    TelegramTeamBridgeConfig,
)


def test_registry_reads_multiple_run_json_files_and_filters_by_session(tmp_path) -> None:
    root = tmp_path / "team_runs"
    older = datetime(2026, 1, 1, 12, tzinfo=UTC)
    newer = older + timedelta(hours=1)
    _write_run(root, "team_run_old", created_at=older, session_id="100", status="failed")
    _write_run(root, "team_run_new", created_at=newer, session_id="100", status="completed")
    _write_run(
        root,
        "team_run_cancelled",
        created_at=older - timedelta(hours=1),
        session_id="100",
        status="cancelled",
    )
    _write_run(root, "team_run_other", created_at=newer, session_id="200", status="completed")

    registry = TeamRunRegistry(root)
    entries = registry.latest_runs(session_id="100")

    assert [entry.run_id for entry in entries] == [
        "team_run_new",
        "team_run_old",
        "team_run_cancelled",
    ]
    assert entries[0].job_id == "job_team_run_new"
    assert entries[0].chat_id == "100"
    assert [entry.run_id for entry in registry.latest_runs(chat_id=100)] == [
        "team_run_new",
        "team_run_old",
        "team_run_cancelled",
    ]
    assert registry.find("team_run_new").status == "completed"
    assert registry.last_completed(session_id="100").run_id == "team_run_new"
    assert registry.last_failed(session_id="100").run_id == "team_run_old"
    assert registry.last_cancelled(session_id="100").run_id == "team_run_cancelled"


def test_registry_sorts_runs_by_finished_started_created_time(tmp_path) -> None:
    root = tmp_path / "team_runs"
    base = datetime(2026, 1, 1, 12, tzinfo=UTC)
    _write_run(root, "team_run_created", created_at=base)
    _write_run(root, "team_run_started", created_at=base, started_at=base + timedelta(minutes=5))
    _write_run(root, "team_run_finished", created_at=base, finished_at=base + timedelta(minutes=10))

    entries = TeamRunRegistry(root).latest_runs()

    assert [entry.run_id for entry in entries] == [
        "team_run_finished",
        "team_run_started",
        "team_run_created",
    ]


def test_registry_marks_corrupted_run_json_without_failing(tmp_path) -> None:
    root = tmp_path / "team_runs"
    bad_dir = root / "team_run_bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "run.json").write_text("{not-json", encoding="utf-8")

    entries = TeamRunRegistry(root).latest_runs(include_invalid=True)

    assert len(entries) == 1
    assert entries[0].run_id == "team_run_bad"
    assert entries[0].corrupted is True
    assert entries[0].invalid is True


def test_status_after_restart_like_state_uses_disk_registry(tmp_path) -> None:
    async def scenario() -> None:
        root = tmp_path / "team_runs"
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(provider="fake", workspace_root=root),
        )
        await bridge.handle_text(chat_id=100, text="сделай краткий план AI-команды")
        completed = await asyncio.wait_for(bridge.jobs.wait("100"), timeout=1)
        await bridge.drain_watchers()

        restarted_bot = RecordingTelegramBot()
        restarted_bridge = TelegramTeamBridge(
            bot=restarted_bot,
            config=TelegramTeamBridgeConfig(provider="fake", workspace_root=root),
        )
        response = await restarted_bridge.handle_text(chat_id=100, text="/status")

        assert completed.status == TeamJobStatus.COMPLETED
        assert response.status == TeamRuntimeStatus.COMPLETED
        assert response.run_id == completed.run_id
        assert "Последний run: completed." in restarted_bot.messages[-1].text
        assert str(completed.workspace_path) in restarted_bot.messages[-1].text
        assert "artifacts:" in restarted_bot.messages[-1].text
        assert "primary_artifact:" in restarted_bot.messages[-1].text

    asyncio.run(scenario())


def test_runs_request_does_not_start_new_team_run(tmp_path) -> None:
    async def scenario() -> None:
        provider = FakeTeamProvider()
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(provider="fake", workspace_root=tmp_path / "runs"),
            provider_factory=lambda: provider,
        )

        response = await bridge.handle_text(chat_id=100, text="/runs")

        assert response.decision.intent.value == "runs_request"
        assert response.status == TeamRuntimeStatus.IDLE
        assert provider.calls == []
        assert bridge.jobs.snapshot("100") is None

    asyncio.run(scenario())


def test_telegram_bridge_answers_runs_from_registry(tmp_path) -> None:
    async def scenario() -> None:
        root = tmp_path / "team_runs"
        _write_run(
            root,
            "team_run_1",
            created_at=datetime(2026, 1, 1, 12, tzinfo=UTC),
            session_id="100",
            status="completed",
            user_task="сделай краткий план AI-команды",
        )
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(provider="fake", workspace_root=root),
        )

        response = await bridge.handle_text(chat_id=100, text="/runs")

        assert response.decision.intent.value == "runs_request"
        assert "Последние запуски команды" in bot.messages[-1].text
        assert "team_run_1" in bot.messages[-1].text
        assert "сделай краткий план AI-команды" in bot.messages[-1].text
        assert "artifacts:" in bot.messages[-1].text

    asyncio.run(scenario())


def test_runs_handles_legacy_run_json_without_artifact_fields(tmp_path) -> None:
    async def scenario() -> None:
        root = tmp_path / "team_runs"
        _write_run(root, "team_run_legacy", session_id="100", include_artifacts=False)
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(provider="fake", workspace_root=root),
        )

        response = await bridge.handle_text(chat_id=100, text="/runs")

        assert response.decision.intent.value == "runs_request"
        assert response.status == TeamRuntimeStatus.IDLE
        assert "team_run_legacy" in bot.messages[-1].text
        assert "artifacts: 0" in bot.messages[-1].text

    asyncio.run(scenario())


def test_runs_ignores_corrupted_run_json_without_crashing(tmp_path) -> None:
    async def scenario() -> None:
        root = tmp_path / "team_runs"
        _write_run(root, "team_run_ok", session_id="100")
        bad_dir = root / "team_run_bad"
        bad_dir.mkdir(parents=True)
        (bad_dir / "run.json").write_text("{broken", encoding="utf-8")
        bot = RecordingTelegramBot()
        bridge = TelegramTeamBridge(
            bot=bot,
            config=TelegramTeamBridgeConfig(provider="fake", workspace_root=root),
        )

        response = await bridge.handle_text(chat_id=100, text="/runs")

        assert response.decision.intent.value == "runs_request"
        assert response.status == TeamRuntimeStatus.IDLE
        assert "team_run_ok" in bot.messages[-1].text
        assert "team_run_bad" not in bot.messages[-1].text

    asyncio.run(scenario())


def test_runs_preview_cli_works_without_nodriver(tmp_path, capsys) -> None:
    sys.modules.pop("astra_nexus.team.nodriver_provider", None)
    root = tmp_path / "team_runs"
    _write_run(root, "team_run_1", session_id="100", status="completed")

    exit_code = run_registry_module.main(["--workspace-root", str(root), "--session-id", "100"])

    output = capsys.readouterr().out
    source = inspect.getsource(run_registry_module)
    assert exit_code == 0
    assert "team_run_1" in output
    assert "NoDriver" not in source
    assert "nodriver" not in source
    assert "astra_nexus.team.nodriver_provider" not in sys.modules


def _write_run(
    root,
    run_id: str,
    *,
    status: str = "completed",
    user_task: str = "сделай краткий план",
    created_at: datetime | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    session_id: str = "100",
    include_artifacts: bool = True,
) -> None:
    created_at = created_at or datetime(2026, 1, 1, 12, tzinfo=UTC)
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    payload = {
        "run_id": run_id,
        "status": status,
        "user_task": user_task,
        "title": user_task,
        "created_at": created_at.isoformat(),
        "started_at": started_at.isoformat() if started_at else None,
        "finished_at": finished_at.isoformat() if finished_at else created_at.isoformat(),
        "final_result": f"final:{run_id}",
        "error_message": None,
        "workspace_path": str(run_dir),
        "session_id": session_id,
        "chat_id": session_id,
        "job_id": f"job_{run_id}",
        "provider": "fake",
        "intent": "new_task",
        "execution_mode": "sequential",
        "runtime_metadata": {
            "session_id": session_id,
            "chat_id": session_id,
            "job_id": f"job_{run_id}",
            "provider": "fake",
        },
    }
    if include_artifacts:
        payload.update(
            {
                "artifacts_count": 2,
                "artifacts_dir": str(run_dir / "artifacts"),
                "primary_artifact_path": str(run_dir / "artifacts" / "final_answer.md"),
                "artifacts_index_path": str(run_dir / "artifacts" / "index.md"),
            }
        )
    (run_dir / "run.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
