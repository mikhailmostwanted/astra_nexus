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
    user_message = "не удалось подключиться к запущенному браузеру"
    action = "запусти astra-nexus-nodriver-diagnose и проверь Chrome/browser profile"


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
