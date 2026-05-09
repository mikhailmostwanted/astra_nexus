from __future__ import annotations

from astra_nexus.brain.base import BrainProviderError


class NoDriverProviderError(BrainProviderError):
    status = "unavailable"
    user_message = "провайдер ChatGPT Web недоступен"
    action = "проверь настройки NoDriver и состояние браузерного профиля"


class NoDriverDependencyError(NoDriverProviderError):
    status = "unavailable"
    user_message = "пакет nodriver не установлен"
    action = "установи зависимости проекта: pip install -e ."


class NoDriverBrowserConnectError(NoDriverProviderError):
    status = "browser_connect_failed"
    user_message = "не удалось подключиться к Chrome через NoDriver"
    action = (
        "закрой лишние окна Chrome, выполни astra-nexus-nodriver-clean "
        "и повтори astra-nexus-nodriver-smoke"
    )


class NoDriverProfileLockedError(NoDriverProviderError):
    status = "profile_locked"
    user_message = "Chrome profile занят другим процессом"
    action = "заверши astra-nexus-nodriver-login/smoke или выполни astra-nexus-nodriver-clean"

    def __init__(
        self,
        message: str | None = None,
        *,
        pid: int | None = None,
        context: str | None = None,
        user_data_dir: str | None = None,
        lock_path: str | None = None,
    ) -> None:
        details = []
        if pid is not None:
            details.append(f"PID {pid}")
        if context:
            details.append(f"context: {context}")
        if user_data_dir:
            details.append(f"profile: {user_data_dir}")
        if lock_path:
            details.append(f"lock: {lock_path}")
        suffix = f" ({', '.join(details)})" if details else ""
        super().__init__(
            message
            or (
                f"NoDriver profile already in use{suffix}. "
                "Закрой предыдущий astra-nexus-nodriver-login/smoke "
                "или заверши процесс PID выше, затем выполни astra-nexus-nodriver-clean."
            ),
            action=(
                f"заверши процесс PID {pid} или выполни astra-nexus-nodriver-clean"
                if pid is not None
                else self.action
            ),
        )


class NoDriverStaleLockCleaned(NoDriverProviderError):
    status = "stale_lock_cleaned"
    user_message = "устаревший NoDriver lock удалён"
    action = "повтори команду NoDriver"


class NoDriverChromeStartTimeoutError(NoDriverProviderError):
    status = "chrome_start_timeout"
    user_message = "Chrome не запустился за отведённое время"
    action = "увеличь NODRIVER_START_TIMEOUT_SECONDS и выполни astra-nexus-nodriver-clean"

    def __init__(self, message: str | None = None, *, timeout_seconds: int | None = None) -> None:
        timeout_hint = (
            f" за {timeout_seconds} секунд"
            if timeout_seconds is not None
            else " за отведённое время"
        )
        super().__init__(message or f"Chrome не запустился{timeout_hint}.")


class NoDriverLoginRequiredError(NoDriverProviderError):
    status = "login_required"
    user_message = "требуется ручной вход в ChatGPT"
    action = "запусти astra-nexus-nodriver-login и авторизуйся в ChatGPT"


class NoDriverTimeoutError(NoDriverProviderError):
    status = "timeout"
    user_message = "истекло время ожидания ответа ChatGPT Web"
    action = "проверь страницу ChatGPT и повторить задачу позже"


class NoDriverSelectorNotFoundError(NoDriverProviderError):
    status = "selector_not_found"
    user_message = "не найден ожидаемый элемент интерфейса ChatGPT"
    action = "проверь docs/NODRIVER.md и обнови селекторы под текущий UI"


class NoDriverPageLoadError(NoDriverProviderError):
    status = "unavailable"
    user_message = "страница ChatGPT не загрузилась"
    action = "проверь сеть, доступность chatgpt.com и браузерный профиль"
