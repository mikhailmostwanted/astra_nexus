from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from astra_nexus.brain.nodriver.browser_session import BrowserSession
from astra_nexus.brain.nodriver.evaluate import unwrap_evaluate_result
from astra_nexus.brain.nodriver.exceptions import (
    NoDriverLoginRequiredError,
    NoDriverProviderError,
    NoDriverTimeoutError,
)
from astra_nexus.brain.nodriver.lifecycle import NoDriverLifecycleManager
from astra_nexus.brain.nodriver.selectors import LOGIN_REQUIRED_QUERY
from astra_nexus.config.settings import Settings


@dataclass(frozen=True)
class BrainHealth:
    status: str
    provider: str
    message: str
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "provider": self.provider,
            "message": self.message,
        }
        if self.details:
            payload.update(self.details)
        return payload


def inspect_nodriver_health(settings: Settings) -> BrainHealth:
    lifecycle = NoDriverLifecycleManager(settings, context="health")
    snapshot = lifecycle.inspect()
    status = "profile_locked" if snapshot.profile_locked else "configured"
    message = (
        "NoDriver настроен, браузер не запускался. "
        "Для реальной проверки запусти astra-nexus-nodriver-smoke "
        "или GET /api/brain/health/deep."
    )
    last_error: str | None = None
    if snapshot.profile_locked:
        message = (
            "NoDriver profile занят другим процессом. "
            "Закрой login/smoke/API deep health или выполни astra-nexus-nodriver-clean."
        )
        last_error = "profile_locked"
    if snapshot.invalid_lock:
        last_error = "invalid_lock"

    browser_path = settings.nodriver_browser_executable_path
    if browser_path is not None and not browser_path.expanduser().exists():
        status = "unavailable"
        last_error = f"Chrome executable не найден: {browser_path}"

    lock_info = snapshot.lock_info
    details: dict[str, Any] = {
        "user_data_dir": str(snapshot.user_data_dir),
        "user_data_dir_exists": snapshot.user_data_dir_exists,
        "profile_locked": snapshot.profile_locked,
        "lock_file": str(snapshot.lock_path),
        "lock_pid": lock_info.pid if lock_info else None,
        "lock_context": lock_info.context if lock_info else None,
        "headless": settings.nodriver_headless,
        "chatgpt_url": settings.nodriver_chatgpt_url,
        "deep_health": "/api/brain/health/deep",
    }
    if last_error:
        details["last_error"] = last_error

    return BrainHealth(
        status=status,
        provider="nodriver",
        message=message,
        details=details,
    )


async def check_nodriver_deep_health(settings: Settings) -> BrainHealth:
    session = BrowserSession(settings, lifecycle_context="deep_health")
    try:
        tab = await session.open_chatgpt()
        if bool(unwrap_evaluate_result(await tab.evaluate(LOGIN_REQUIRED_QUERY))):
            raise NoDriverLoginRequiredError()
        return BrainHealth(
            status="ok",
            provider="nodriver",
            message="ChatGPT Web доступен.",
            details={
                "user_data_dir": str(session.user_data_dir),
                "profile_locked": False,
                "headless": settings.nodriver_headless,
                "chatgpt_url": settings.nodriver_chatgpt_url,
            },
        )
    except NoDriverTimeoutError as exc:
        return BrainHealth(
            status=exc.status,
            provider="nodriver",
            message=str(exc),
            details={"action": exc.action},
        )
    except NoDriverProviderError as exc:
        return BrainHealth(
            status=exc.status,
            provider="nodriver",
            message=str(exc),
            details={"action": exc.action},
        )
    finally:
        await session.stop()
