import asyncio
from pathlib import Path

from astra_nexus.brain.nodriver import login, smoke
from astra_nexus.config.settings import Settings


class FakeSession:
    stopped = False

    def __init__(self, *_args, **_kwargs) -> None:
        self.user_data_dir = Path("/tmp/astra-nexus-test-profile")

    async def open_chatgpt(self) -> object:
        return object()

    async def stop(self) -> None:
        self.stopped = True


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        nodriver_user_data_dir=tmp_path / "profile",
        nodriver_keep_browser_open_on_error=False,
    )


def test_login_helper_does_not_return_ok_without_composer_or_login_proof(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    async def fake_collect(_session) -> dict:
        return {
            "ready_state": "complete",
            "textarea_count": 0,
            "contenteditable_count": 0,
            "textbox_count": 0,
            "login_buttons_count": 0,
            "login_button_count": 0,
            "candidate_count": 0,
            "composer_found": False,
            "login_state": "chatgpt_ui_not_ready",
        }

    monkeypatch.setattr(login, "load_settings", lambda: make_settings(tmp_path))
    monkeypatch.setattr(login, "BrowserSession", FakeSession)
    monkeypatch.setattr(login, "collect_dom_probe", fake_collect)
    monkeypatch.setattr(login.asyncio, "to_thread", _instant_input)

    exit_code = asyncio.run(login.amain())

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "status: chatgpt_ui_not_ready" in output
    assert "status: ok" not in output


def test_smoke_does_not_search_prompt_box_when_login_required(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    class FailingClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def ask(self, _prompt: str) -> str:
            raise AssertionError("smoke не должен отправлять prompt без входа")

    async def fake_collect(_session) -> dict:
        return {
            "current_url": "https://chatgpt.com/",
            "page_title": "ChatGPT",
            "ready_state": "complete",
            "textarea_count": 0,
            "contenteditable_count": 0,
            "textbox_count": 0,
            "login_buttons_count": 2,
            "login_button_count": 2,
            "candidate_count": 0,
            "composer_found": False,
            "login_state": "login_required",
        }

    monkeypatch.setattr(smoke, "load_settings", lambda: make_settings(tmp_path))
    monkeypatch.setattr(smoke, "BrowserSession", FakeSession)
    monkeypatch.setattr(smoke, "ChatGPTClient", FailingClient)
    monkeypatch.setattr(smoke, "collect_dom_probe", fake_collect)

    exit_code = asyncio.run(smoke.amain())

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "status: login_required" in output
    assert "candidate_count: 0" in output


def test_smoke_cancelled_error_returns_without_traceback(monkeypatch, tmp_path, capsys) -> None:
    async def cancelled_collect(_session) -> dict:
        raise asyncio.CancelledError

    monkeypatch.setattr(smoke, "load_settings", lambda: make_settings(tmp_path))
    monkeypatch.setattr(smoke, "BrowserSession", FakeSession)
    monkeypatch.setattr(smoke, "collect_dom_probe", cancelled_collect)

    exit_code = asyncio.run(smoke.amain())

    output = capsys.readouterr().out
    assert exit_code == 130
    assert "Остановлено пользователем." in output


async def _instant_input(_func, *_args, **_kwargs):
    return ""
