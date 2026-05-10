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


def test_team_atmosphere_settings_accept_requested_env_aliases(monkeypatch) -> None:
    monkeypatch.setenv("TEAM_ATMOSPHERE_ENABLED", "false")
    monkeypatch.setenv("TEAM_ATMOSPHERE_LEVEL", "cinematic")
    monkeypatch.setenv("TEAM_ATMOSPHERE_SEND_DELAYS", "true")
    monkeypatch.setenv("TEAM_ATMOSPHERE_MIN_DELAY_SECONDS", "0.1")
    monkeypatch.setenv("TEAM_ATMOSPHERE_MAX_DELAY_SECONDS", "0.9")
    monkeypatch.setenv("TEAM_ATMOSPHERE_EMOJI_ENABLED", "true")
    monkeypatch.setenv("TEAM_ATMOSPHERE_MAX_MAIN_MESSAGES_PER_RUN", "5")
    monkeypatch.setenv("TEAM_ATMOSPHERE_SUPPRESS_TECHNICAL_IN_MAIN", "false")

    settings = Settings(_env_file=None)

    assert settings.team_atmosphere_enabled is False
    assert settings.team_atmosphere_level == "cinematic"
    assert settings.team_atmosphere_send_delays is True
    assert settings.team_atmosphere_min_delay_seconds == 0.1
    assert settings.team_atmosphere_max_delay_seconds == 0.9
    assert settings.team_atmosphere_emoji_enabled is True
    assert settings.team_atmosphere_max_main_messages_per_run == 5
    assert settings.team_atmosphere_suppress_technical_in_main is False


def test_nodriver_window_settings_defaults_are_safe() -> None:
    settings = Settings(_env_file=None)

    assert settings.nodriver_window_mode == "small"
    assert settings.nodriver_window_width == 1100
    assert settings.nodriver_window_height == 800
    assert settings.nodriver_window_x == 20
    assert settings.nodriver_window_y == 20
    assert settings.nodriver_background_start is True
    assert settings.nodriver_disable_focus_stealing is True
