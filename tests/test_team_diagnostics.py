from __future__ import annotations

import asyncio
import inspect
import sys

from pydantic import SecretStr

from astra_nexus.config.settings import Settings
from astra_nexus.team import diagnostics as diagnostics_module
from astra_nexus.team.diagnostics import DiagnosticStatus, run_mvp_diagnostics


def test_mvp_check_does_not_require_telegram_api(tmp_path, capsys) -> None:
    sys.modules.pop("aiogram", None)
    settings = Settings(
        team_telegram_provider="fake",
        telegram_bot_token=None,
        team_runs_dir=tmp_path / "team_runs",
        team_telegram_downloads_dir=tmp_path / "downloads",
    )

    exit_code = diagnostics_module.main([], settings=settings)

    output = capsys.readouterr().out
    source = inspect.getsource(diagnostics_module)
    assert exit_code == 0
    assert "Astra Nexus AI Team MVP check" in output
    assert "TELEGRAM_BOT_TOKEN" in output
    assert "warn" in output
    assert "aiogram" not in source
    assert "aiogram" not in sys.modules


def test_mvp_check_passes_with_fake_provider(tmp_path) -> None:
    settings = Settings(
        team_telegram_provider="fake",
        telegram_bot_token=SecretStr("123:fake-token"),
        team_telegram_allowed_chat_ids="100",
        team_telegram_log_chat_id=200,
        team_runs_dir=tmp_path / "team_runs",
        team_telegram_downloads_dir=tmp_path / "downloads",
    )

    report = asyncio.run(run_mvp_diagnostics(settings=settings))

    assert report.status == DiagnosticStatus.OK
    checks = {check.name: check for check in report.checks}
    assert checks["fake provider short run"].status == DiagnosticStatus.OK
    assert checks["artifacts dir"].status == DiagnosticStatus.OK
    assert settings.team_runs_dir.exists()
    assert settings.team_telegram_downloads_dir.exists()


def test_mvp_check_for_nodriver_does_not_import_or_start_nodriver(tmp_path) -> None:
    sys.modules.pop("astra_nexus.team.nodriver_provider", None)
    settings = Settings(
        team_telegram_provider="nodriver",
        telegram_bot_token=SecretStr("123:fake-token"),
        team_telegram_allowed_chat_ids="100",
        team_runs_dir=tmp_path / "team_runs",
        team_telegram_downloads_dir=tmp_path / "downloads",
    )

    report = asyncio.run(run_mvp_diagnostics(settings=settings))

    checks = {check.name: check for check in report.checks}
    assert checks["nodriver live readiness"].status == DiagnosticStatus.WARN
    assert "astra-nexus-nodriver-smoke" in "\n".join(report.next_actions)
    assert "astra_nexus.team.nodriver_provider" not in sys.modules
