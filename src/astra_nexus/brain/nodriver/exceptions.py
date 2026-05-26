from __future__ import annotations

from astra_nexus.brain.base import BrainProviderError


class NoDriverProviderError(BrainProviderError):
    status = "unavailable"
    user_message = "провайдер ChatGPT Web недоступен"
    action = "проверь настройки NoDriver и состояние браузерного профиля"

    def __init__(
        self,
        message: str | None = None,
        *,
        action: str | None = None,
        stage: str | None = None,
        url: str | None = None,
        selector: str | None = None,
        page_title: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, action=action)
        self.stage = stage
        self.url = url
        self.selector = selector
        self.page_title = page_title
        self.details = details or {}

    @property
    def error_code(self) -> str:
        return self.status


class NoDriverDependencyError(NoDriverProviderError):
    status = "unavailable"
    user_message = "пакет nodriver не установлен"
    action = "установи зависимости проекта: pip install -e ."


class NoDriverBrowserConnectError(NoDriverProviderError):
    status = "browser_connect_failed"
    user_message = "не удалось подключиться к Chrome через NoDriver"
    action = (
        "запусти astra-nexus-nodriver-doctor; при занятом profile выполни "
        "astra-nexus-nodriver-clean; если нужен вход, запусти astra-nexus-nodriver-login; "
        "проверь Chrome и повтори astra-nexus-nodriver-smoke"
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
        stage: str | None = None,
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
            stage=stage,
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
    status = "response_timeout"
    user_message = "истекло время ожидания ответа ChatGPT Web"
    action = "проверь страницу ChatGPT и повторить задачу позже"


class NoDriverPreferredModelError(NoDriverProviderError):
    status = "preferred_model_not_active"
    user_message = "в ChatGPT Web выбран не тот режим модели"
    action = "выбери нужную модель/режим в ChatGPT Web или отключи NODRIVER_REQUIRE_PREFERRED_MODEL"


class NoDriverSelectorNotFoundError(NoDriverProviderError):
    status = "selector_not_found"
    user_message = "не найден ожидаемый элемент интерфейса ChatGPT"
    action = "проверь docs/NODRIVER.md и обнови селекторы под текущий UI"


class NoDriverPromptBoxNotFoundError(NoDriverSelectorNotFoundError):
    status = "prompt_box_not_found"
    user_message = "поле ввода ChatGPT не найдено"
    action = (
        "проверь, что открыт ChatGPT после входа, затем запусти "
        "astra-nexus-nodriver-ask для диагностики"
    )


class NoDriverArtifactInputPromptBoxNotFoundError(NoDriverProviderError):
    status = "artifact_input_prompt_box_not_found"
    user_message = "Поле ввода ChatGPT (composer) не найдено для загрузки артефакта."
    action = "Проверь, что ChatGPT открыт и интерфейс загрузки доступен."


class NoDriverArtifactInputUploadButtonNotFoundError(NoDriverProviderError):
    status = "artifact_input_upload_button_not_found"
    user_message = "Кнопка загрузки файлов не найдена в интерфейсе ChatGPT."
    action = "Проверь, доступна ли загрузка файлов в твоем аккаунте ChatGPT."


class NoDriverArtifactInputUploadFailedError(NoDriverProviderError):
    status = "artifact_input_upload_failed"
    user_message = "Не удалось завершить загрузку файлов в ChatGPT."
    action = "Попробуй перезагрузить страницу или проверь формат файлов."


class NoDriverArtifactInputUploadTimeoutError(NoDriverProviderError):
    status = "artifact_input_upload_timeout"
    user_message = "Истекло время ожидания подтверждения загрузки файлов."
    action = "Проверь скорость соединения или попробуй уменьшить размер файлов."


class NoDriverArtifactInputUploadFilenameMismatchError(NoDriverProviderError):
    status = "artifact_input_upload_filename_mismatch"
    user_message = "Загруженный файл не соответствует ожидаемому имени."
    action = "Убедись, что файлы не повреждены и имеют корректные расширения."


class NoDriverChatGPTUINotReadyError(NoDriverPromptBoxNotFoundError):
    status = "chatgpt_ui_not_ready"
    user_message = "интерфейс ChatGPT Web не готов"
    action = (
        "проверь страницу ChatGPT, затем запусти astra-nexus-nodriver-dom-probe "
        "для диагностики текущего DOM"
    )


class NoDriverPromptInsertFailedError(NoDriverProviderError):
    status = "prompt_insert_failed"
    user_message = "не удалось вставить prompt в поле ввода ChatGPT"
    action = (
        "запусти astra-nexus-nodriver-dom-probe и проверь, что composer ChatGPT доступен для ввода"
    )


class NoDriverArtifactDownloadError(NoDriverProviderError):
    status = "requested_file_missing"
    user_message = "ChatGPT Web не создал скачиваемый файл"
    action = (
        "проверь artifact_detector_debug.json и повтори задачу; ChatGPT должен показать "
        "file card или download button"
    )


class NoDriverPageLoadError(NoDriverProviderError):
    status = "unavailable"
    user_message = "страница ChatGPT не загрузилась"
    action = "проверь сеть, доступность chatgpt.com и браузерный профиль"
