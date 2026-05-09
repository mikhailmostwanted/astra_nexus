import asyncio
from pathlib import Path

import pytest

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.chatgpt_client import ChatGPTClient
from astra_nexus.brain.nodriver.exceptions import (
    NoDriverBrowserConnectError,
    NoDriverChatGPTUINotReadyError,
)
from astra_nexus.config.settings import Settings


class FakeBrowser:
    def __init__(self) -> None:
        self.get_calls: list[str] = []

    async def get(self, url: str) -> object:
        self.get_calls.append(url)
        return FakeTab(url)


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
