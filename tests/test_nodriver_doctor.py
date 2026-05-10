import asyncio
from pathlib import Path

from astra_nexus.brain.nodriver import doctor
from astra_nexus.config.settings import Settings


class FakeDoctorSession:
    def __init__(self, settings: Settings, lifecycle_context: str) -> None:
        self.settings = settings
        self.lifecycle_context = lifecycle_context
        self.user_data_dir = Path(settings.nodriver_user_data_dir).expanduser().resolve()
        self.start_diagnostics = [
            {
                "attempt": 1,
                "remote_debugging_host": "127.0.0.1",
                "remote_debugging_port": 9222,
                "window_mode": "normal",
                "minimal_args_mode": True,
                "chrome_process_started": True,
                "endpoint_open": True,
                "endpoint_waited_seconds": 0.4,
                "chrome_args": [],
            }
        ]
        self.started = False
        self.stopped = False

    async def start(self) -> object:
        self.started = True
        return object()

    async def stop(self) -> None:
        self.stopped = True


def test_nodriver_doctor_starts_browser_without_ask_and_prints_diagnostics(
    tmp_path: Path,
    capsys,
) -> None:
    sessions: list[FakeDoctorSession] = []

    def session_factory(settings: Settings, lifecycle_context: str) -> FakeDoctorSession:
        session = FakeDoctorSession(settings, lifecycle_context)
        sessions.append(session)
        return session

    settings = Settings(
        data_dir=tmp_path / "data",
        nodriver_user_data_dir=tmp_path / "profile",
        nodriver_window_mode="normal",
    )

    exit_code = asyncio.run(doctor.arun(settings=settings, session_factory=session_factory))

    output = capsys.readouterr().out
    assert exit_code == 0
    assert sessions[0].started is True
    assert sessions[0].stopped is True
    assert "status: ok" in output
    assert "remote_debugging_port: 9222" in output
    assert "endpoint_open: true" in output
    assert "window_mode: normal" in output
