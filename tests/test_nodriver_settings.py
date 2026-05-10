from astra_nexus.config.settings import Settings


def test_keep_browser_open_on_error_setting_defaults_to_false() -> None:
    settings = Settings(_env_file=None)

    assert settings.nodriver_keep_browser_open_on_error is False


def test_keep_browser_open_on_error_setting_can_be_enabled() -> None:
    settings = Settings(nodriver_keep_browser_open_on_error=True)

    assert settings.nodriver_keep_browser_open_on_error is True


def test_start_retry_backoff_setting_accepts_env_alias(monkeypatch) -> None:
    monkeypatch.setenv("NODRIVER_START_RETRY_BACKOFF_SECONDS", "4.5")

    settings = Settings(_env_file=None)

    assert settings.nodriver_start_retry_delay_seconds == 4.5
    assert settings.nodriver_start_retry_backoff_seconds == 4.5


def test_start_lifecycle_settings_accept_requested_env_aliases(monkeypatch) -> None:
    monkeypatch.setenv("NODRIVER_START_RETRIES", "5")
    monkeypatch.setenv("NODRIVER_START_RETRY_DELAY_SECONDS", "3.5")
    monkeypatch.setenv("NODRIVER_AFTER_TERMINATE_GRACE_SECONDS", "1.25")

    settings = Settings(_env_file=None)

    assert settings.nodriver_start_retry_attempts == 5
    assert settings.nodriver_start_retries == 5
    assert settings.nodriver_start_retry_delay_seconds == 3.5
    assert settings.nodriver_after_terminate_grace_seconds == 1.25


def test_team_telegram_runtime_settings_accept_requested_env_aliases(monkeypatch) -> None:
    monkeypatch.setenv("TEAM_TELEGRAM_PROVIDER", "nodriver")
    monkeypatch.setenv("TEAM_TELEGRAM_ALLOWED_CHAT_IDS", "100,200")
    monkeypatch.setenv("TEAM_TELEGRAM_LOG_CHAT_ID", "300")
    monkeypatch.setenv("TEAM_TELEGRAM_DOWNLOADS_DIR", "./data/tg_downloads")
    monkeypatch.setenv("TEAM_TELEGRAM_SEND_TYPING", "false")
    monkeypatch.setenv("TEAM_TELEGRAM_MAX_FILE_SIZE_MB", "7")
    monkeypatch.setenv("TEAM_TELEGRAM_HUMAN_MESSAGES", "false")

    settings = Settings(_env_file=None)

    assert settings.team_telegram_provider == "nodriver"
    assert settings.team_telegram_allowed_chat_ids == "100,200"
    assert settings.team_telegram_log_chat_id == 300
    assert str(settings.team_telegram_downloads_dir) == "data/tg_downloads"
    assert settings.team_telegram_send_typing is False
    assert settings.team_telegram_max_file_size_mb == 7
    assert settings.team_telegram_human_messages is False
