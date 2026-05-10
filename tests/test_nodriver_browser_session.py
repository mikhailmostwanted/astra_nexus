import asyncio
from pathlib import Path

import pytest

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.chatgpt_client import ChatGPTClient
from astra_nexus.brain.nodriver.exceptions import (
    NoDriverBrowserConnectError,
    NoDriverChatGPTUINotReadyError,
)
from astra_nexus.brain.nodriver.lifecycle import NoDriverLifecycleManager, ProcessInfo
from astra_nexus.config.settings import Settings


class FakeBrowser:
    def __init__(self) -> None:
        self.get_calls: list[str] = []
        self.stopped = False

    async def get(self, url: str) -> object:
        self.get_calls.append(url)
        return FakeTab(url)

    def stop(self) -> None:
        self.stopped = True


class AsyncStopBrowser(FakeBrowser):
    async def stop(self) -> None:
        self.stopped = True


class CloseOnlyBrowser(FakeBrowser):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeTab:
    def __init__(
        self,
        url: str,
        title: str = "ChatGPT",
        evaluations: dict[str, object] | None = None,
    ) -> None:
        self.url = url
        self.title = title
        self.evaluations = evaluations or {}

    async def evaluate(self, script: str) -> object:
        for marker, value in self.evaluations.items():
            if marker in script:
                return value
        if "window.location.href" in script:
            return self.url
        if "document.title" in script:
            return self.title
        return ""


def test_browser_session_resolves_user_data_dir_to_absolute_path(tmp_path: Path) -> None:
    settings = Settings(nodriver_user_data_dir=tmp_path / "profile")
    session = BrowserSession(settings=settings, start_browser=lambda **_: FakeBrowser())

    assert session.user_data_dir.is_absolute()
    assert session.user_data_dir == (tmp_path / "profile").expanduser().resolve()


def test_browser_session_builds_start_kwargs_with_timeout_no_sandbox_and_executable(
    tmp_path: Path,
) -> None:
    def fake_start(*, sandbox: bool = True, **_: object) -> FakeBrowser:
        return FakeBrowser()

    settings = Settings(
        nodriver_user_data_dir=tmp_path / "profile",
        nodriver_headless=True,
        nodriver_start_timeout_seconds=45,
        nodriver_no_sandbox=True,
        nodriver_browser_executable_path=tmp_path / "Chrome",
    )
    session = BrowserSession(settings=settings, start_browser=fake_start)

    kwargs = session.build_start_kwargs()

    assert kwargs["user_data_dir"] == str((tmp_path / "profile").resolve())
    assert kwargs["headless"] is True
    assert kwargs["start_timeout"] == 45
    assert kwargs["sandbox"] is False
    assert kwargs["browser_executable_path"] == str((tmp_path / "Chrome").resolve())


def test_browser_session_small_window_mode_adds_size_and_position(tmp_path: Path) -> None:
    settings = Settings(
        nodriver_user_data_dir=tmp_path / "profile",
        nodriver_window_mode="small",
        nodriver_window_width=900,
        nodriver_window_height=700,
        nodriver_window_x=30,
        nodriver_window_y=40,
    )
    session = BrowserSession(settings=settings, start_browser=lambda **_: FakeBrowser())

    kwargs = session.build_start_kwargs()

    assert kwargs["headless"] is False
    assert kwargs["browser_args"] == ["--window-size=900,700", "--window-position=30,40"]


def test_browser_session_offscreen_window_mode_adds_offscreen_position(tmp_path: Path) -> None:
    settings = Settings(
        nodriver_user_data_dir=tmp_path / "profile",
        nodriver_window_mode="offscreen",
    )
    session = BrowserSession(settings=settings, start_browser=lambda **_: FakeBrowser())

    kwargs = session.build_start_kwargs()

    assert kwargs["headless"] is False
    assert kwargs["browser_args"] == ["--window-size=1100,800", "--window-position=-32000,-32000"]


def test_browser_session_normal_window_mode_does_not_add_window_args(tmp_path: Path) -> None:
    settings = Settings(
        nodriver_user_data_dir=tmp_path / "profile",
        nodriver_window_mode="normal",
    )
    session = BrowserSession(settings=settings, start_browser=lambda **_: FakeBrowser())

    kwargs = session.build_start_kwargs()

    assert kwargs["headless"] is False
    assert "browser_args" not in kwargs


def test_browser_session_headless_mode_is_explicit_only(tmp_path: Path) -> None:
    default_settings = Settings(nodriver_user_data_dir=tmp_path / "default")
    default_session = BrowserSession(
        settings=default_settings,
        start_browser=lambda **_: FakeBrowser(),
    )
    headless_settings = Settings(
        nodriver_user_data_dir=tmp_path / "headless",
        nodriver_window_mode="headless",
    )
    headless_session = BrowserSession(
        settings=headless_settings,
        start_browser=lambda **_: FakeBrowser(),
    )

    assert default_session.build_start_kwargs()["headless"] is False
    assert headless_session.build_start_kwargs()["headless"] is True


def test_browser_session_login_context_keeps_manual_window_visible(tmp_path: Path) -> None:
    settings = Settings(
        nodriver_user_data_dir=tmp_path / "profile",
        nodriver_window_mode="offscreen",
    )
    session = BrowserSession(
        settings=settings,
        start_browser=lambda **_: FakeBrowser(),
        lifecycle_context="login",
    )

    kwargs = session.build_start_kwargs()

    assert kwargs["headless"] is False
    assert kwargs["browser_args"] == ["--window-size=1100,800", "--window-position=20,20"]


def test_browser_session_maps_repeated_start_failures_to_browser_connect_failed(
    tmp_path: Path,
) -> None:
    attempts = 0

    async def failing_start(**_: object) -> FakeBrowser:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("Failed to connect to browser")

    settings = Settings(
        nodriver_user_data_dir=tmp_path / "profile",
        nodriver_start_retry_attempts=2,
        nodriver_start_retry_delay_seconds=0,
    )
    session = BrowserSession(settings=settings, start_browser=failing_start)

    with pytest.raises(NoDriverBrowserConnectError) as exc:
        asyncio.run(session.start())

    assert attempts == 2
    assert exc.value.status == "browser_connect_failed"
    assert "Failed to connect to browser" in str(exc.value)


def test_browser_session_retries_browser_connect_with_safe_window_fallback(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[dict[str, object]] = []

    async def flaky_start(**kwargs: object) -> FakeBrowser:
        calls.append(dict(kwargs))
        if len(calls) == 1:
            raise RuntimeError("Failed to connect to browser")
        return FakeBrowser()

    settings = Settings(
        data_dir=tmp_path / "data",
        nodriver_user_data_dir=tmp_path / "profile",
        nodriver_window_mode="small",
        nodriver_window_width=900,
        nodriver_window_height=700,
        nodriver_window_x=30,
        nodriver_window_y=40,
        nodriver_start_retry_attempts=2,
        nodriver_start_retry_delay_seconds=0,
    )
    session = BrowserSession(settings=settings, start_browser=flaky_start)

    browser = asyncio.run(session.start())

    assert isinstance(browser, FakeBrowser)
    assert calls[0]["browser_args"] == ["--window-size=900,700", "--window-position=30,40"]
    assert "browser_args" not in calls[1]
    assert "fallback window mode" in caplog.text


def test_browser_session_cleans_runtime_and_chrome_locks_between_start_retries(
    tmp_path: Path,
) -> None:
    attempts = 0
    profile = tmp_path / "profile"

    async def flaky_start(**_: object) -> FakeBrowser:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            for filename in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
                (profile / filename).write_text("lock", encoding="utf-8")
            raise RuntimeError("Failed to connect to browser")
        return FakeBrowser()

    settings = Settings(
        data_dir=tmp_path / "data",
        nodriver_user_data_dir=profile,
        nodriver_start_retry_attempts=2,
        nodriver_start_retry_delay_seconds=0,
    )
    session = BrowserSession(settings=settings, start_browser=flaky_start)

    browser = asyncio.run(session.start())

    assert isinstance(browser, FakeBrowser)
    assert attempts == 2
    assert session.lifecycle.read_lock() is not None
    for filename in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        assert not (profile / filename).exists()


def test_browser_session_terminates_own_failed_start_process_before_retry(
    tmp_path: Path,
) -> None:
    attempts = 0
    profile = tmp_path / "profile"
    live_pids: set[int] = set()
    terminated_pids: list[int] = []

    async def flaky_start(**_: object) -> FakeBrowser:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            live_pids.add(4242)
            (profile / "SingletonLock").write_text("lock", encoding="utf-8")
            raise RuntimeError("Failed to connect to browser")
        return FakeBrowser()

    def find_processes(_profile: Path) -> list[ProcessInfo]:
        return [
            ProcessInfo(pid=pid, command=f"Chrome --user-data-dir={profile}")
            for pid in sorted(live_pids)
        ]

    def terminate_process(pid: int) -> None:
        terminated_pids.append(pid)
        live_pids.discard(pid)

    settings = Settings(
        data_dir=tmp_path / "data",
        nodriver_user_data_dir=profile,
        nodriver_start_retry_attempts=2,
        nodriver_start_retry_delay_seconds=0,
        nodriver_after_terminate_grace_seconds=0,
    )
    lifecycle = NoDriverLifecycleManager(
        settings,
        context="provider",
        find_profile_processes=find_processes,
        is_pid_alive=lambda pid: pid in live_pids,
        terminate_process=terminate_process,
    )
    session = BrowserSession(settings=settings, start_browser=flaky_start, lifecycle=lifecycle)

    browser = asyncio.run(session.start())

    assert isinstance(browser, FakeBrowser)
    assert attempts == 2
    assert terminated_pids == [4242]
    assert live_pids == set()
    assert session.lifecycle.read_lock() is not None
    assert not (profile / "SingletonLock").exists()


def test_browser_session_reports_browser_connect_failed_when_own_process_stays_locked(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"
    live_pids: set[int] = set()

    async def failing_start(**_: object) -> FakeBrowser:
        live_pids.add(5252)
        (profile / "SingletonLock").write_text("lock", encoding="utf-8")
        raise RuntimeError("Failed to connect to browser")

    def find_processes(_profile: Path) -> list[ProcessInfo]:
        return [
            ProcessInfo(pid=pid, command=f"Chrome --user-data-dir={profile}")
            for pid in sorted(live_pids)
        ]

    settings = Settings(
        data_dir=tmp_path / "data",
        nodriver_user_data_dir=profile,
        nodriver_start_retry_attempts=2,
        nodriver_start_retry_delay_seconds=0,
        nodriver_after_terminate_grace_seconds=0,
    )
    lifecycle = NoDriverLifecycleManager(
        settings,
        context="provider",
        find_profile_processes=find_processes,
        is_pid_alive=lambda pid: pid in live_pids,
        terminate_process=lambda _pid: None,
        kill_process=lambda _pid: None,
    )
    session = BrowserSession(settings=settings, start_browser=failing_start, lifecycle=lifecycle)

    with pytest.raises(NoDriverBrowserConnectError) as exc:
        asyncio.run(session.start())

    assert exc.value.status == "browser_connect_failed"
    assert "astra-nexus-nodriver-clean" in exc.value.action
    assert session.lifecycle.read_lock() is None
    assert (profile / "SingletonLock").exists()


def test_browser_session_releases_lock_after_all_start_retries_fail(tmp_path: Path) -> None:
    async def failing_start(**_: object) -> FakeBrowser:
        (tmp_path / "profile/SingletonLock").write_text("lock", encoding="utf-8")
        raise RuntimeError("Failed to connect to browser")

    settings = Settings(
        data_dir=tmp_path / "data",
        nodriver_user_data_dir=tmp_path / "profile",
        nodriver_start_retry_attempts=1,
        nodriver_start_retry_delay_seconds=0,
    )
    session = BrowserSession(settings=settings, start_browser=failing_start)

    with pytest.raises(NoDriverBrowserConnectError):
        asyncio.run(session.start())

    assert session.lifecycle.read_lock() is None
    assert not (tmp_path / "profile/SingletonLock").exists()


def test_browser_session_releases_lock_when_start_is_cancelled(tmp_path: Path) -> None:
    async def cancelled_start(**_: object) -> FakeBrowser:
        raise asyncio.CancelledError()

    settings = Settings(
        data_dir=tmp_path / "data",
        nodriver_user_data_dir=tmp_path / "profile",
        nodriver_start_retry_attempts=2,
        nodriver_start_retry_delay_seconds=0,
    )
    session = BrowserSession(settings=settings, start_browser=cancelled_start)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(session.start())

    assert session.lifecycle.read_lock() is None


def test_browser_session_awaits_async_stop_and_releases_lock(tmp_path: Path) -> None:
    browser = AsyncStopBrowser()
    settings = Settings(nodriver_user_data_dir=tmp_path / "profile")
    session = BrowserSession(settings=settings, start_browser=lambda **_: browser)

    asyncio.run(session.start())
    asyncio.run(session.stop())

    assert browser.stopped is True
    assert session.lifecycle.read_lock() is None


def test_browser_session_uses_close_fallback_and_releases_lock(tmp_path: Path) -> None:
    class StopFailingBrowser(CloseOnlyBrowser):
        def stop(self) -> None:
            raise RuntimeError("stop failed")

    browser = StopFailingBrowser()
    settings = Settings(nodriver_user_data_dir=tmp_path / "profile")
    session = BrowserSession(settings=settings, start_browser=lambda **_: browser)

    asyncio.run(session.start())
    asyncio.run(session.stop())

    assert browser.closed is True
    assert session.lifecycle.read_lock() is None


def test_browser_session_ensure_chatgpt_page_does_not_reload_existing_chatgpt_tab(
    tmp_path: Path,
) -> None:
    settings = Settings(nodriver_user_data_dir=tmp_path / "profile")
    session = BrowserSession(settings=settings, start_browser=lambda **_: FakeBrowser())
    browser = FakeBrowser()
    session.browser = browser
    session.tab = FakeTab("https://chatgpt.com/c/123")

    tab = asyncio.run(session.ensure_chatgpt_page())

    assert tab is session.tab
    assert browser.get_calls == []


def test_browser_session_ensure_chatgpt_page_opens_when_current_tab_is_blank(
    tmp_path: Path,
) -> None:
    settings = Settings(nodriver_user_data_dir=tmp_path / "profile")
    session = BrowserSession(settings=settings, start_browser=lambda **_: FakeBrowser())
    browser = FakeBrowser()
    session.browser = browser
    session.tab = FakeTab("about:blank")

    tab = asyncio.run(session.ensure_chatgpt_page())

    assert isinstance(tab, FakeTab)
    assert browser.get_calls == [settings.nodriver_chatgpt_url]


def test_login_unknown_without_composer_is_not_treated_as_login_ok(tmp_path: Path) -> None:
    settings = Settings(
        nodriver_user_data_dir=tmp_path / "profile",
        nodriver_page_load_timeout_seconds=0.01,
    )
    session = BrowserSession(settings=settings, start_browser=lambda **_: FakeBrowser())
    session.browser = FakeBrowser()
    session.tab = FakeTab(
        "https://chatgpt.com/",
        evaluations={
            "LOGIN_STATE_PROBE": {
                "login_required": False,
                "login_ok": False,
                "reason": "unknown",
            },
            "PROMPT_CANDIDATE_PROBE": {
                "ready_state": "complete",
                "textarea_count": 0,
                "contenteditable_count": 0,
                "textbox_count": 0,
                "candidate_count": 0,
                "visible_candidates": [],
                "marked_selector": None,
            },
            "document.readyState": "complete",
        },
    )
    client = ChatGPTClient(settings=settings, session=session)

    with pytest.raises(NoDriverChatGPTUINotReadyError) as exc:
        asyncio.run(client.ask("Проверка"))

    assert exc.value.details["login_state"]["reason"] == "unknown"
    assert exc.value.status == "chatgpt_ui_not_ready"
