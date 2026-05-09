import asyncio

import pytest

from astra_nexus.brain.nodriver.exceptions import (
    NoDriverBrowserConnectError,
    NoDriverChromeStartTimeoutError,
    NoDriverLoginRequiredError,
    NoDriverProfileLockedError,
    NoDriverTimeoutError,
)
from astra_nexus.brain.nodriver_provider import NoDriverProvider
from astra_nexus.config.settings import Settings


class FailingClient:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def ask(self, prompt: str) -> str:
        raise self.exc


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

    assert exc.value.status == "timeout"
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
