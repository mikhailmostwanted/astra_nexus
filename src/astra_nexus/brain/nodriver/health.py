from __future__ import annotations

from dataclasses import dataclass

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.exceptions import (
    NoDriverLoginRequiredError,
    NoDriverProviderError,
    NoDriverTimeoutError,
)
from astra_nexus.brain.nodriver.selectors import LOGIN_REQUIRED_QUERY
from astra_nexus.config.settings import Settings


@dataclass(frozen=True)
class BrainHealth:
    status: str
    provider: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"status": self.status, "provider": self.provider, "message": self.message}


async def check_nodriver_health(settings: Settings) -> BrainHealth:
    session = BrowserSession(settings)
    try:
        tab = await session.open_chatgpt()
        if bool(await tab.evaluate(LOGIN_REQUIRED_QUERY)):
            raise NoDriverLoginRequiredError()
        return BrainHealth(status="ok", provider="nodriver", message="ChatGPT Web доступен.")
    except NoDriverTimeoutError as exc:
        return BrainHealth(status=exc.status, provider="nodriver", message=str(exc))
    except NoDriverProviderError as exc:
        return BrainHealth(status=exc.status, provider="nodriver", message=str(exc))
    finally:
        await session.stop()
