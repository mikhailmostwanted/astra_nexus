import asyncio
import json
from pathlib import Path

import pytest

from astra_nexus.brain.nodriver.exceptions import (
    NoDriverBrowserConnectError,
    NoDriverChromeStartTimeoutError,
    NoDriverLoginRequiredError,
    NoDriverProfileLockedError,
    NoDriverPromptBoxNotFoundError,
    NoDriverTimeoutError,
)
from astra_nexus.brain.nodriver_provider import NoDriverProvider
from astra_nexus.config.settings import Settings


class FailingClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def ask(self, prompt: str, **_: object) -> str:
        raise self.exc


class SlowClient:
    def __init__(self) -> None:
        self.active = 0
        self.overlapped = False

    async def ask(self, prompt: str, **_: object) -> str:
        self.active += 1
        if self.active > 1:
            self.overlapped = True
        await asyncio.sleep(0.05)
        self.active -= 1
        return "ok"


class RecordingClient:
    def __init__(self) -> None:
        self.prompt = ""

    async def ask(self, prompt: str, **_: object) -> str:
        self.prompt = prompt
        return "ok"


def test_nodriver_provider_maps_login_required_error() -> None:
    provider = NoDriverProvider(
        settings=Settings(brain_provider="nodriver"),
        client=FailingClient(NoDriverLoginRequiredError("Нужен вход")),
    )

    with pytest.raises(NoDriverLoginRequiredError) as exc:
        asyncio.run(provider.ask(agent_id="writer", prompt="Промпт"))

    assert exc.value.status == "login_required"
    assert "astra-nexus-nodriver-login" in exc.value.action


def test_nodriver_provider_maps_timeout_error() -> None:
    provider = NoDriverProvider(
        settings=Settings(brain_provider="nodriver"),
        client=FailingClient(NoDriverTimeoutError("Истекло время ожидания")),
    )

    with pytest.raises(NoDriverTimeoutError) as exc:
        asyncio.run(provider.ask(agent_id="writer", prompt="Промпт"))

    assert exc.value.status == "response_timeout"
    assert "повторить" in exc.value.action.lower()


def test_nodriver_provider_maps_browser_connect_failed_error() -> None:
    provider = NoDriverProvider(
        settings=Settings(brain_provider="nodriver"),
        client=FailingClient(NoDriverBrowserConnectError("Failed to connect to browser")),
    )

    with pytest.raises(NoDriverBrowserConnectError) as exc:
        asyncio.run(provider.ask(agent_id="writer", prompt="Промпт"))

    assert exc.value.status == "browser_connect_failed"
    assert "astra-nexus-nodriver-clean" in exc.value.action


def test_nodriver_provider_maps_profile_locked_error() -> None:
    provider = NoDriverProvider(
        settings=Settings(brain_provider="nodriver"),
        client=FailingClient(NoDriverProfileLockedError(pid=12345, context="login")),
    )

    with pytest.raises(NoDriverProfileLockedError) as exc:
        asyncio.run(provider.ask(agent_id="writer", prompt="Промпт"))

    assert exc.value.status == "profile_locked"
    assert "12345" in str(exc.value)
    assert "astra-nexus-nodriver-clean" in exc.value.action


def test_nodriver_provider_maps_chrome_start_timeout_error() -> None:
    provider = NoDriverProvider(
        settings=Settings(brain_provider="nodriver"),
        client=FailingClient(NoDriverChromeStartTimeoutError(timeout_seconds=90)),
    )

    with pytest.raises(NoDriverChromeStartTimeoutError) as exc:
        asyncio.run(provider.ask(agent_id="writer", prompt="Промпт"))

    assert exc.value.status == "chrome_start_timeout"
    assert "90" in str(exc.value)


def test_nodriver_provider_creates_debug_report_on_nodriver_error(tmp_path: Path) -> None:
    workspace_path = tmp_path / "task_123"
    provider = NoDriverProvider(
        settings=Settings(
            brain_provider="nodriver",
            workspace_base_path=tmp_path,
            nodriver_debug_screenshots=False,
        ),
        client=FailingClient(
            NoDriverPromptBoxNotFoundError(
                "Поле ввода ChatGPT не найдено.",
                stage="chatgpt.prompt_box.search.started",
                url="https://chatgpt.com/",
                selector="#prompt-textarea",
                details={
                    "ready_state": "complete",
                    "textarea_count": 0,
                    "contenteditable_count": 0,
                    "textbox_count": 0,
                    "candidate_count": 0,
                    "selectors_tried": ["#prompt-textarea"],
                    "visible_candidates": [],
                },
            )
        ),
    )

    with pytest.raises(NoDriverPromptBoxNotFoundError):
        asyncio.run(
            provider.ask(
                agent_id="writer",
                prompt="Промпт",
                context={
                    "task_id": "task_123",
                    "run_id": "run_456",
                    "workspace_path": workspace_path,
                },
            )
        )

    report_path = workspace_path / "debug/nodriver_error.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["task_id"] == "task_123"
    assert payload["run_id"] == "run_456"
    assert payload["agent_id"] == "writer"
    assert payload["stage"] == "chatgpt.prompt_box.search.started"
    assert payload["provider"] == "nodriver"
    assert payload["error_code"] == "prompt_box_not_found"
    assert payload["message"] == "Поле ввода ChatGPT не найдено."
    assert payload["url"] == "https://chatgpt.com/"
    assert payload["selector"] == "#prompt-textarea"
    assert payload["ready_state"] == "complete"
    assert payload["textarea_count"] == 0
    assert payload["contenteditable_count"] == 0
    assert payload["textbox_count"] == 0
    assert payload["candidate_count"] == 0
    assert payload["selectors_tried"] == ["#prompt-textarea"]
    assert payload["visible_candidates"] == []
    assert "screenshot_path" not in payload


def test_nodriver_provider_serializes_parallel_ask_calls() -> None:
    client = SlowClient()
    provider = NoDriverProvider(settings=Settings(brain_provider="nodriver"), client=client)

    async def run_two_calls() -> None:
        await asyncio.gather(
            provider.ask(agent_id="writer", prompt="Первый"),
            provider.ask(agent_id="critic", prompt="Второй"),
        )

    asyncio.run(run_two_calls())

    assert client.overlapped is False


def test_nodriver_provider_supports_direct_prompt_context() -> None:
    client = RecordingClient()
    provider = NoDriverProvider(settings=Settings(brain_provider="nodriver"), client=client)

    response = asyncio.run(
        provider.ask(
            agent_id="manual",
            prompt="Ответь ровно так: Astra Nexus online.",
            context={"direct_prompt": True},
        )
    )

    assert response.content == "ok"
    assert client.prompt == "Ответь ровно так: Astra Nexus online."
